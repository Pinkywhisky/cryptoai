import os
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.financial_migrations import prepare_financial_migration, apply_financial_schema, _version, FINANCIAL_SCHEMA_VERSION, validate_financial_schema
from services import sync_migrations


DB_PATH = Path(
    os.getenv("CRYPTOAI_DB_PATH")
    or Path(__file__).resolve().parent.parent / "database" / "history.db"
)
RESETTABLE_TABLES = [
    "positions",
    "watch_candidates",
    "journal_entries",
    "trade_journal",
    "alerts",
    "market_history",
    "binance_balances",
    "binance_trades",
    "binance_sync_state",
    "exchange_sync_state",
    "binance_trade_sync",
    "financial_sync_lock",
    "historical_fx_rates",
    "binance_job_markets",
    "binance_sync_jobs",
    "binance_sync_previews",
    "binance_market_catalog",
    "binance_catalog_state",
    "score_snapshots",
    "backtest_results",
    "market_cache",
]


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


_INITIALIZE_LOCK = threading.RLock()
_READY_SCHEMAS = {}


def _schema_ready(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    identity=path.stat()
    key=(str(path.resolve()),identity.st_dev,identity.st_ino)
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,timeout=10)) as connection:
        cookie=connection.execute("PRAGMA schema_version").fetchone()[0]
        if _version(connection)!=FINANCIAL_SCHEMA_VERSION or sync_migrations.version(connection)!=sync_migrations.SYNC_SCHEMA_VERSION:
            return False
        if _READY_SCHEMAS.get(key)==cookie:
            return True
        validate_financial_schema(connection)
        sync_migrations.validate(connection)
        _READY_SCHEMAS[key]=cookie
        return True


def init_db(db_path: Path | str = DB_PATH, *, allow_financial_migration: bool = False) -> None:
    # Normal reads only inspect the schema cookie, without DDL or a write lock.
    path=Path(db_path)
    if _schema_ready(path):
        return
    with _INITIALIZE_LOCK:
        if not _schema_ready(path):
            _initialize_db(path,allow_financial_migration=allow_financial_migration)
            if not _schema_ready(path):
                raise RuntimeError("Database initialization failed")


def _initialize_db(db_path: Path | str = DB_PATH, *, allow_financial_migration: bool = False) -> None:
    path = Path(db_path)
    prepare_financial_migration(path, allow_existing=allow_financial_migration)
    sync_migrations.prepare(path, allow_existing=allow_financial_migration)
    path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                global_score INTEGER NOT NULL,
                global_signal TEXT NOT NULL,
                current_price REAL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                quantity REAL NOT NULL,
                average_buy_price REAL NOT NULL,
                invested_amount REAL NOT NULL,
                strategy_type TEXT,
                take_profit_1 REAL,
                take_profit_2 REAL,
                stop_loss REAL,
                max_loss_accepted_pct REAL,
                note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        for column, definition in [
            ("current_price", "REAL"),
            ("current_value", "REAL"),
            ("source", "TEXT DEFAULT 'MANUAL'"),
            ("binance_asset", "TEXT"),
            ("synced_from_binance", "INTEGER DEFAULT 0"),
            ("is_active", "INTEGER DEFAULT 1"),
            ("status", "TEXT DEFAULT 'MANUAL'"),
            ("last_sync_at", "TEXT"),
            ("realized_pnl", "REAL DEFAULT 0"),
            ("unrealized_pnl", "REAL DEFAULT 0"),
            ("total_pnl", "REAL DEFAULT 0"),
            ("profit_loss_pct", "REAL"),
            ("is_estimated", "INTEGER DEFAULT 0"),
            ("market_source", "TEXT"),
            ("resolved_symbol", "TEXT"),
            ("base_asset", "TEXT"),
        ]:
            _add_column_if_missing(connection, "positions", column, definition)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS watch_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                target_buy_price REAL,
                invalidation_price REAL,
                priority TEXT,
                reason TEXT,
                note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        for column, definition in [
            ("market_source", "TEXT"),
            ("resolved_symbol", "TEXT"),
            ("base_asset", "TEXT"),
        ]:
            _add_column_if_missing(connection, "watch_candidates", column, definition)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market_source TEXT,
                decision_type TEXT,
                trade_type TEXT,
                emotion TEXT,
                confidence INTEGER,
                reason TEXT,
                notes TEXT,
                result_status TEXT DEFAULT 'OPEN',
                pnl_percent REAL,
                pnl_usdc REAL,
                duration_minutes INTEGER,
                ai_score INTEGER,
                ai_trend TEXT,
                ai_setup_quality TEXT,
                ai_trigger TEXT,
                ai_market_regime TEXT,
                ai_market_score INTEGER,
                ai_snapshot_json TEXT
            )
            """
        )
        for column, definition in [
            ("market_source", "TEXT"),
            ("decision_type", "TEXT"),
            ("trade_type", "TEXT"),
            ("emotion", "TEXT"),
            ("confidence", "INTEGER"),
            ("reason", "TEXT"),
            ("notes", "TEXT"),
            ("result_status", "TEXT DEFAULT 'OPEN'"),
            ("pnl_percent", "REAL"),
            ("pnl_usdc", "REAL"),
            ("duration_minutes", "INTEGER"),
            ("ai_score", "INTEGER"),
            ("ai_trend", "TEXT"),
            ("ai_setup_quality", "TEXT"),
            ("ai_trigger", "TEXT"),
            ("ai_market_regime", "TEXT"),
            ("ai_market_score", "INTEGER"),
            ("ai_snapshot_json", "TEXT"),
        ]:
            _add_column_if_missing(connection, "journal_entries", column, definition)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT,
                confidence INTEGER,
                emotion TEXT,
                result TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS binance_balances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset TEXT NOT NULL UNIQUE,
                free REAL NOT NULL,
                locked REAL NOT NULL,
                total REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS binance_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                order_id INTEGER,
                trade_id INTEGER NOT NULL UNIQUE,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                qty REAL NOT NULL,
                quote_qty REAL NOT NULL,
                commission REAL,
                commission_asset TEXT,
                time INTEGER,
                is_buyer INTEGER NOT NULL,
                is_maker INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS binance_sync_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_sync TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS score_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                global_score INTEGER,
                context_score INTEGER,
                setup_score INTEGER,
                trigger_score INTEGER,
                risk_score INTEGER,
                market_score INTEGER,
                confidence INTEGER,
                decision TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                decision TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                outcome TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_cache (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        apply_financial_schema(connection)
        sync_migrations.apply(connection)
        connection.commit()


def reset_database(db_path: Path | str = DB_PATH) -> dict[str, Any]:
    init_db(db_path)
    summary: dict[str, Any] = {}

    with sqlite3.connect(db_path) as connection:
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

        for table in RESETTABLE_TABLES:
            if table not in existing_tables:
                summary[table] = {"existed": False, "deleted": 0}
                continue

            count_row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            deleted = int(count_row[0])
            connection.execute(f"DELETE FROM {table}")
            summary[table] = {"existed": True, "deleted": deleted}

        sequence_tables = [
            table
            for table in RESETTABLE_TABLES
            if table in existing_tables and table != "binance_sync_state"
        ]
        if sequence_tables and "sqlite_sequence" in existing_tables:
            placeholders = ", ".join("?" for _ in sequence_tables)
            connection.execute(
                f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                sequence_tables,
            )

        connection.commit()

    return summary


def save_scan_result(
    result: dict[str, Any],
    db_path: Path | str = DB_PATH,
) -> None:
    if "error" in result:
        return

    init_db(db_path)
    timestamp = datetime.now(UTC).isoformat()

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO market_history (
                timestamp,
                symbol,
                global_score,
                global_signal,
                current_price
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                result["symbol"],
                int(result["global_score"]),
                result["global_signal"],
                result.get("current_price"),
            ),
        )
        connection.commit()


def get_symbol_history(
    symbol: str,
    limit: int = 50,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    init_db(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT timestamp, global_score, current_price
            FROM market_history
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (symbol.upper(), limit),
        ).fetchall()

    return [
        {
            "timestamp": row["timestamp"],
            "score": row["global_score"],
            "price": row["current_price"],
        }
        for row in rows
    ]
