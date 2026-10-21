"""Migrate only synthetic copies of the exact pre-lot-3 schema from Git."""
import hashlib
from pathlib import Path
import sqlite3

import pytest

from services import financial_migrations
from services.financial_migrations import MigrationRequired
from services.history import init_db


def legacy_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript((Path(__file__).parent/"fixtures/financial_schema_v0.sql").read_text())
    with sqlite3.connect(path) as connection:
        connection.execute("""INSERT INTO binance_trades
            (symbol,order_id,trade_id,side,price,qty,quote_qty,commission,
             commission_asset,time,is_buyer,is_maker)
            VALUES('ABCUSDC',8,7,'BUY',?,?,?,?, 'USDC',1,1,0)""",
            (1.2345678901234567,0.10000000000000002,0.12345678901234568,0.0))
        connection.execute("""INSERT INTO positions
            (symbol,quantity,average_buy_price,invested_amount,note,created_at,updated_at)
            VALUES('MANUAL',3,1,3,'synthetic private text','before','before')""")
        connection.execute("UPDATE sqlite_sequence SET seq=99 WHERE name='binance_trades'")
        connection.execute("CREATE INDEX custom_order_index ON binance_trades(order_id)")
    return path


def table_rows(path,table):
    with sqlite3.connect(path) as connection:
        connection.row_factory=sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM "+table)]


def test_migration_preserves_rows_precision_and_backup(tmp_path):
    path=legacy_database(tmp_path/"legacy.sqlite")
    before=table_rows(path,"binance_trades")[0]
    manual_before=table_rows(path,"positions")[0]
    init_db(path,allow_financial_migration=True)
    after=table_rows(path,"binance_trades")[0]
    for name,value in before.items():
        assert (float(after[name]) if name in {"price","qty","quote_qty","commission"} else after[name])==value
    assert after["price"]=="1.2345678901234567"
    assert after["precision_status"]=="LEGACY_FLOAT"
    manual_after=table_rows(path,"positions")[0]
    assert all(manual_after[name]==value for name,value in manual_before.items())
    backups=list(tmp_path.glob("legacy.sqlite.pre-finance-v1-*.sqlite"))
    assert len(backups)==1
    assert backups[0].stat().st_mode & 0o777==0o600
    assert table_rows(backups[0],"binance_trades")[0]==before
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall()==[]
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]==1
        assert connection.execute("SELECT seq FROM sqlite_sequence WHERE name='binance_trades'").fetchone()[0]==99
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='custom_order_index'").fetchone()
    init_db(path,allow_financial_migration=True)
    assert len(list(tmp_path.glob("legacy.sqlite.pre-finance-v1-*.sqlite")))==1
    assert len(table_rows(path,"schema_migrations"))==1


def test_new_trade_constraint_is_per_exchange_and_market(tmp_path):
    path=legacy_database(tmp_path/"legacy.sqlite")
    init_db(path,allow_financial_migration=True)
    sql="""INSERT INTO binance_trades(exchange,symbol,order_id,trade_id,side,
        price,qty,quote_qty,commission,commission_asset,time,is_buyer,is_maker)
        VALUES(?,?,1,7,'BUY','1','1','1','0','USDC',2,1,0)"""
    with sqlite3.connect(path) as connection:
        connection.execute(sql,("BINANCE_SPOT","XYZUSDC"))
        connection.execute(sql,("OTHER_EXCHANGE","ABCUSDC"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(sql,("BINANCE_SPOT","ABCUSDC"))
    assert len(table_rows(path,"binance_trades"))==3


def test_unknown_cost_can_be_null_without_losing_manual_rows(tmp_path):
    path=legacy_database(tmp_path/"legacy.sqlite")
    init_db(path,allow_financial_migration=True)
    with sqlite3.connect(path) as connection:
        connection.execute("""INSERT INTO positions(symbol,quantity,average_buy_price,
            invested_amount,created_at,updated_at) VALUES('UNKNOWN',1,NULL,NULL,'now','now')""")
    assert table_rows(path,"positions")[1]["invested_amount"] is None
    assert table_rows(path,"positions")[0]["note"]=="synthetic private text"


def test_migration_failure_rolls_back_all_ddl_and_preserves_backup(tmp_path,monkeypatch):
    path=legacy_database(tmp_path/"legacy.sqlite")
    before=table_rows(path,"binance_trades")
    real=financial_migrations._rebuild
    def injected(connection,table,transform):
        if table=="positions":
            raise MigrationRequired("synthetic failure")
        return real(connection,table,transform)
    monkeypatch.setattr(financial_migrations,"_rebuild",injected)
    with pytest.raises(MigrationRequired):
        init_db(path,allow_financial_migration=True)
    assert table_rows(path,"binance_trades")==before
    with sqlite3.connect(path) as connection:
        names={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "schema_migrations" not in names
        assert not any("finance_v1" in name for name in names)
    assert len(list(tmp_path.glob("legacy.sqlite.pre-finance-v1-*.sqlite")))==1
    monkeypatch.setattr(financial_migrations,"_rebuild",real)
    init_db(path,allow_financial_migration=True)
    assert len(table_rows(path,"binance_trades"))==1


def test_designated_personal_database_is_never_migrated(tmp_path,monkeypatch):
    # The guard is exercised against a synthetic stand-in, never the real DB.
    path=legacy_database(tmp_path/"personal-stand-in.sqlite")
    monkeypatch.setattr(financial_migrations,"PERSONAL_DATABASE",path)
    before=(hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_mtime_ns)
    with pytest.raises(MigrationRequired):
        init_db(path,allow_financial_migration=True)
    assert (hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_mtime_ns)==before
    assert list(tmp_path.glob("personal-stand-in.sqlite.pre-*"))==[]


def test_hardlinked_migration_target_is_rejected(tmp_path):
    import os
    source=legacy_database(tmp_path/"source.sqlite")
    linked=tmp_path/"linked.sqlite"
    os.link(source,linked)
    with pytest.raises(MigrationRequired,match="hard-linked"):
        init_db(linked,allow_financial_migration=True)
    assert len(table_rows(source,"binance_trades"))==1


def test_future_schema_version_is_not_downgraded(tmp_path):
    path=tmp_path/"future.sqlite"
    init_db(path,allow_financial_migration=True)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO schema_migrations VALUES(999,'future','synthetic')")
    with pytest.raises(MigrationRequired,match="newer"):
        init_db(path,allow_financial_migration=True)


def test_custom_global_unique_index_is_rejected_instead_of_silently_losing_trades(tmp_path):
    path=legacy_database(tmp_path/"legacy.sqlite")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE UNIQUE INDEX custom_bad_identity ON binance_trades(trade_id)")
    with pytest.raises(MigrationRequired,match="global trade-id"):
        init_db(path,allow_financial_migration=True)
    assert len(table_rows(path,"binance_trades"))==1
    with sqlite3.connect(path) as connection:
        assert "exchange" not in {row[1] for row in connection.execute("PRAGMA table_info(binance_trades)")}


def test_custom_trigger_and_foreign_key_rows_survive_migration(tmp_path):
    path=legacy_database(tmp_path/"legacy.sqlite")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE dependent(position_id INTEGER REFERENCES positions(id),label TEXT)")
        connection.execute("INSERT INTO dependent VALUES(1,'synthetic')")
        connection.execute("CREATE TABLE audit_marker(value INTEGER)")
        connection.execute("""CREATE TRIGGER preserve_trigger AFTER INSERT ON binance_trades
                            BEGIN INSERT INTO audit_marker VALUES(new.trade_id); END""")
    init_db(path,allow_financial_migration=True)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dependent").fetchone()[0]==1
        assert connection.execute("PRAGMA foreign_key_check").fetchall()==[]
        connection.execute("""INSERT INTO binance_trades
            (symbol,order_id,trade_id,side,price,qty,quote_qty,commission,
             commission_asset,time,is_buyer,is_maker)
            VALUES('XYZUSDC',1,9,'BUY','1','1','1','0','USDC',2,1,0)""")
        assert connection.execute("SELECT value FROM audit_marker").fetchone()[0]==9


def test_existing_legacy_file_requires_explicit_opt_in(tmp_path):
    path=legacy_database(tmp_path/"legacy.sqlite")
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(MigrationRequired,match="explicit opt-in"):
        init_db(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert not list(tmp_path.glob("legacy.sqlite.pre-*"))


def test_copy_only_tool_check_apply_and_idempotence(tmp_path):
    from tools.migrate_financial import run
    path=legacy_database(tmp_path/"isolated.sqlite")
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    pending=run(path)
    assert not pending["success"]
    assert pending["error"]=="MIGRATION_REQUIRED"
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    migrated=run(path,apply=True)
    assert migrated["success"]
    assert migrated["historical_counts_preserved"]
    assert migrated["backups_retained"]==1
    assert run(path)["success"]
    assert run(path,apply=True)["backups_retained"]==1


def test_copy_tool_refuses_personal_standin_even_with_apply(tmp_path,monkeypatch):
    from tools.migrate_financial import run
    path=legacy_database(tmp_path/"personal.sqlite")
    monkeypatch.setattr(financial_migrations,"PERSONAL_DATABASE",path)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    assert run(path,apply=True)=={"success":False,"error":"PERSONAL_DATABASE_FORBIDDEN"}
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_incomplete_versioned_schema_is_not_silently_accepted(tmp_path):
    path=tmp_path/"damaged.sqlite"
    init_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX binance_trade_identity")
    with pytest.raises(MigrationRequired,match="uniqueness"):
        init_db(path)


def test_backfill_replaces_only_missing_legacy_metadata_from_authoritative_page(tmp_path):
    from services.binance_trade_history import sync_market_history
    from test_financial_reference import PagedClient
    path=legacy_database(tmp_path/"legacy.sqlite")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE binance_trades SET time=NULL,commission=NULL,commission_asset=NULL")
    init_db(path,allow_financial_migration=True)
    item={"id":7,"orderId":8,"symbol":"ABCUSDC","time":1,"isBuyer":True,"isMaker":False,
          "price":"1.2345678901234567","qty":"0.10000000000000002",
          "quoteQty":"0.12345678901234568","commission":"0","commissionAsset":"USDC"}
    assert sync_market_history("ABCUSDC",PagedClient({"ABCUSDC":[item]}),path)["complete"]
    row=table_rows(path,"binance_trades")[0]
    assert row["id"]==1
    assert row["time"]==1
    assert row["commission"]=="0"
    assert row["precision_status"]=="NATIVE_DECIMAL"
