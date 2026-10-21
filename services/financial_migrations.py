"""Versioned financial schema; never migrate the designated personal database."""
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
import re
import sqlite3
import uuid

PERSONAL_DATABASE = Path(__file__).resolve().parents[1] / "database" / "history.db"
FINANCIAL_SCHEMA_VERSION = 1


class MigrationRequired(RuntimeError):
    pass


def _is_personal(path):
    return Path(path).resolve() == PERSONAL_DATABASE.resolve()


def _version(connection):
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    return (connection.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
            if exists else 0)


def prepare_financial_migration(db_path, *, allow_existing=False):
    path = Path(db_path)
    if path.exists() and path.stat().st_nlink != 1:
        raise MigrationRequired("Migration refuses hard-linked SQLite files")
    if not path.exists() or not path.stat().st_size:
        if _is_personal(path):
            raise MigrationRequired("Personal database migration requires independent approval")
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        version = _version(source)
        if version > FINANCIAL_SCHEMA_VERSION:
            raise MigrationRequired("Database schema is newer than this application")
        if version == FINANCIAL_SCHEMA_VERSION:
            validate_financial_schema(source)
            return None
        if _is_personal(path):
            raise MigrationRequired("Migrate and validate an isolated copy before an approved replacement")
        if not allow_existing:
            raise MigrationRequired("Existing database migration requires explicit opt-in on an isolated copy")
        backup = path.with_name(path.name + ".pre-finance-v1-" + uuid.uuid4().hex + ".sqlite")
        import os
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise MigrationRequired("Pre-migration backup verification failed")
        return backup


def _rebuild(connection, table, transform):
    original = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
    before = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    objects = connection.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
        (table,),
    ).fetchall()
    sequence_row = connection.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
    sequence = sequence_row[0] if sequence_row else 0
    temporary = table + "_finance_v1"
    definition = transform(original)
    definition = re.sub(r'(?i)^CREATE TABLE\s+(?:IF NOT EXISTS\s+)?["\x60\[]?' + re.escape(table) + r'["\x60\]]?',
                        'CREATE TABLE "' + temporary + '"', definition, count=1)
    if temporary not in definition:
        raise MigrationRequired("Unsupported legacy table definition")
    connection.execute(definition)
    quoted = ", ".join('"' + col.replace('"', '""') + '"' for col in columns)
    if table=="binance_trades":
        cursor=connection.execute(f'SELECT {quoted} FROM "{table}"')
        numeric={"price","qty","quote_qty","commission"}
        while batch:=cursor.fetchmany(500):
            normalized=[tuple(str(value) if columns[index] in numeric and isinstance(value,float) else value
                              for index,value in enumerate(row)) for row in batch]
            connection.executemany(f'INSERT INTO "{temporary}" ({quoted}) VALUES('+",".join("?" for _ in columns)+")",normalized)
    else:
        connection.execute(f'INSERT INTO "{temporary}" ({quoted}) SELECT {quoted} FROM "{table}"')
    if connection.execute(f'SELECT COUNT(*) FROM "{temporary}"').fetchone()[0] != before:
        raise MigrationRequired("Migration row-count mismatch")
    connection.execute(f'DROP TABLE "{table}"')
    connection.execute(f'ALTER TABLE "{temporary}" RENAME TO "{table}"')
    for (sql,) in objects:
        connection.execute(sql)
    if sequence:
        connection.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name=?", (sequence,table))


def _add(connection, table, name, definition):
    if name not in {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}:
        connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


def apply_financial_schema(connection):
    version = _version(connection)
    if version == FINANCIAL_SCHEMA_VERSION:
        return
    if version > FINANCIAL_SCHEMA_VERSION:
        raise MigrationRequired("Unsupported future database version")
    database = connection.execute("PRAGMA database_list").fetchone()[2]
    if database and _is_personal(database):
        raise MigrationRequired("Personal database migration is forbidden")
    if not connection.in_transaction:
        raise MigrationRequired("Financial migration requires an explicit transaction")
    def trades_schema(sql):
        sql = re.sub(r"(?i)(trade_id\s+INTEGER\s+NOT NULL)\s+UNIQUE", r"\1", sql)
        for name in ("price","qty","quote_qty","commission"):
            sql = re.sub(r"(?i)(\b" + name + r"\s+)REAL\b", r"\1TEXT", sql)
        return sql
    _rebuild(connection, "binance_trades", trades_schema)
    _add(connection,"binance_trades","exchange","TEXT NOT NULL DEFAULT 'BINANCE_SPOT'")
    _add(connection,"binance_trades","base_asset","TEXT")
    _add(connection,"binance_trades","quote_asset","TEXT")
    _add(connection,"binance_trades","precision_status","TEXT NOT NULL DEFAULT 'LEGACY_FLOAT'")
    connection.execute("CREATE UNIQUE INDEX binance_trade_identity ON binance_trades(exchange,symbol,trade_id)")
    for index in connection.execute("PRAGMA index_list(binance_trades)").fetchall():
        if index[2] and [row[0] for row in connection.execute("SELECT name FROM pragma_index_info(?)",(index[1],))]==["trade_id"]:
            raise MigrationRequired("Unsupported global trade-id uniqueness rule")
    connection.execute("CREATE INDEX binance_trade_chronology ON binance_trades(exchange,time,symbol,trade_id)")
    connection.execute("CREATE INDEX binance_trade_asset ON binance_trades(exchange,base_asset,time)")
    def position_schema(sql):
        for name in ("average_buy_price","invested_amount"):
            sql = re.sub(r"(?i)(\b" + name + r"\s+REAL)\s+NOT NULL", r"\1", sql)
        return sql
    _rebuild(connection,"positions",position_schema)
    for name,definition in [
        ("exchange","TEXT NOT NULL DEFAULT 'BINANCE_SPOT'"),
        ("data_status","TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'"),
        ("history_status","TEXT NOT NULL DEFAULT 'INCOMPLETE'"),
        ("cost_basis_status","TEXT NOT NULL DEFAULT 'UNKNOWN'"),
        ("price_status","TEXT NOT NULL DEFAULT 'UNAVAILABLE'"),
        ("pnl_status","TEXT NOT NULL DEFAULT 'INCOMPLETE'"),
        ("realized_pnl_status","TEXT NOT NULL DEFAULT 'INCOMPLETE'"),
        ("accounting_json","TEXT"),
    ]:
        _add(connection,"positions",name,definition)
    connection.execute("CREATE INDEX position_exchange_asset ON positions(exchange,binance_asset,synced_from_binance)")
    def balance_schema(sql):
        return re.sub(r"(?i)(asset\s+TEXT\s+NOT NULL)\s+UNIQUE",r"\1",sql)
    _rebuild(connection,"binance_balances",balance_schema)
    _add(connection,"binance_balances","exchange","TEXT NOT NULL DEFAULT 'BINANCE_SPOT'")
    for name in ("free_decimal","locked_decimal","total_decimal"):
        _add(connection,"binance_balances",name,"TEXT")
    _add(connection,"binance_balances","value_usdc","TEXT")
    _add(connection,"binance_balances","valuation_status","TEXT NOT NULL DEFAULT 'UNAVAILABLE'")
    connection.execute("CREATE UNIQUE INDEX binance_balance_identity ON binance_balances(exchange,asset)")
    connection.execute("""CREATE TABLE binance_trade_sync(
        exchange TEXT NOT NULL, symbol TEXT NOT NULL,
        next_id INTEGER NOT NULL DEFAULT 0 CHECK(next_id>=0),
        initial_complete INTEGER NOT NULL DEFAULT 0 CHECK(initial_complete IN (0,1)),
        status TEXT NOT NULL DEFAULT 'PENDING', updated_at TEXT, error_code TEXT,
        PRIMARY KEY(exchange,symbol))""")
    connection.execute("""CREATE TABLE exchange_sync_state(
        exchange TEXT PRIMARY KEY, last_sync TEXT, sync_status TEXT NOT NULL DEFAULT 'NEVER',
        last_attempt TEXT, markets_complete INTEGER NOT NULL DEFAULT 0,
        markets_total INTEGER NOT NULL DEFAULT 0, discovery_status TEXT NOT NULL DEFAULT 'INCOMPLETE')""")
    connection.execute("""INSERT INTO exchange_sync_state(exchange,last_sync,sync_status)
        SELECT 'BINANCE_SPOT',last_sync,'LEGACY' FROM binance_sync_state WHERE id=1""")
    connection.execute("""CREATE TABLE financial_sync_lock(
        exchange TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL)""")
    connection.execute("""CREATE TABLE historical_fx_rates(
        asset TEXT NOT NULL, quote_asset TEXT NOT NULL, time INTEGER NOT NULL,
        rate TEXT NOT NULL, source TEXT NOT NULL,
        exchange TEXT NOT NULL DEFAULT 'BINANCE_SPOT',
        PRIMARY KEY(exchange,asset,quote_asset,time))""")
    for name,definition in [
        ("sync_status","TEXT NOT NULL DEFAULT 'NEVER'"),
        ("last_attempt","TEXT"),
        ("markets_complete","INTEGER NOT NULL DEFAULT 0"),
        ("markets_total","INTEGER NOT NULL DEFAULT 0"),
        ("discovery_status","TEXT NOT NULL DEFAULT 'INCOMPLETE'"),
    ]:
        _add(connection,"binance_sync_state",name,definition)
    connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations(
        version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)""")
    validate_financial_schema(connection)
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise MigrationRequired("Foreign-key verification failed")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise MigrationRequired("Post-migration integrity verification failed")
    connection.execute("INSERT INTO schema_migrations VALUES(?,?,?)",
                       (FINANCIAL_SCHEMA_VERSION,"financial reliability",
                        datetime.now(UTC).isoformat()))


def validate_financial_schema(connection):
    required={
        "binance_trades":{"exchange","symbol","trade_id","base_asset","quote_asset","precision_status","price","qty","quote_qty","commission"},
        "positions":{"exchange","data_status","history_status","cost_basis_status","price_status","pnl_status","accounting_json","average_buy_price","invested_amount"},
        "binance_balances":{"exchange","free_decimal","locked_decimal","total_decimal","value_usdc","valuation_status"},
        "binance_trade_sync":{"exchange","symbol","next_id","initial_complete","status"},
        "financial_sync_lock":{"exchange","owner","expires_at"},
        "exchange_sync_state":{"exchange","last_sync","last_attempt","sync_status","discovery_status"},
        "historical_fx_rates":{"exchange","asset","quote_asset","time","rate","source"},
    }
    definitions={}
    for table,columns in required.items():
        actual={row[1]:row for row in connection.execute(f'PRAGMA table_info("{table}")')}
        if not columns<=set(actual):
            raise MigrationRequired("Incomplete financial schema")
        definitions[table]=actual
    for field in ("price","qty","quote_qty","commission"):
        if definitions["binance_trades"][field][2].upper()!="TEXT":
            raise MigrationRequired("Trade decimal storage is not TEXT")
    for field in ("average_buy_price","invested_amount"):
        if definitions["positions"][field][3]:
            raise MigrationRequired("Unknown acquisition costs must be nullable")
    for table,expected in [("binance_trades",("exchange","symbol","trade_id")),
                           ("binance_balances",("exchange","asset"))]:
        identities=[]
        for index in connection.execute(f'PRAGMA index_list("{table}")').fetchall():
            if index[2]:
                identities.append(tuple(row[0] for row in connection.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno",(index[1],))))
        if expected not in identities or (table=="binance_trades" and ("trade_id",) in identities):
            raise MigrationRequired("Invalid financial uniqueness constraints")
