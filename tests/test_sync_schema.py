"""Schema-only opening, versioned copy migration, concurrency and no silent repairs."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import sqlite3
import pytest

from services.history import init_db
from services import sync_migrations
from services.financial_migrations import MigrationRequired
from services.binance_trade_history import connect
from tools.migrate_financial import run
from tools.simulate_sync_cost import _legacy_module


def legacy_database(path):
    old=_legacy_module("_lot3_history","services/history.py")
    old.init_db(path)
    with sqlite3.connect(path) as c:
        c.execute("""INSERT INTO binance_trades(symbol,trade_id,side,price,qty,quote_qty,time,is_buyer,is_maker)
            VALUES('ABCUSDC',1,'BUY','100','1','100',1,1,0)""")
    return path


def test_read_path_does_not_begin_write_or_rebuild_schema(tmp_path,monkeypatch):
    path=tmp_path/"db.sqlite";init_db(path)
    statements=[]
    original=sqlite3.connect
    def traced(*a,**k):
        connection=original(*a,**k)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(sqlite3,"connect",traced)
    for _ in range(5):
        init_db(path)
    assert not any("BEGIN" in x.upper() or "CREATE " in x.upper() or "ALTER " in x.upper() for x in statements)
    assert all(not x.lstrip().upper().startswith(("INSERT","UPDATE","DELETE")) for x in statements)


def test_initialization_check_does_not_wait_for_reserved_writer(tmp_path):
    path=tmp_path/"db.sqlite";init_db(path)
    with connect(path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(lambda:init_db(path)).result(timeout=1) is None


def test_many_threads_can_initialize_new_synthetic_database(tmp_path):
    path=tmp_path/"db.sqlite"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _:init_db(path),range(24)))
    with connect(path) as c:
        assert sync_migrations.version(c)==1
        assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]==1


def test_lot3_schema_requires_explicit_copy_migration_and_preserves_rows(tmp_path):
    path=legacy_database(tmp_path/"legacy.sqlite")
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(MigrationRequired):
        init_db(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert not run(path)["success"]
    result=run(path,apply=True)
    assert result["success"]
    assert result["historical_counts_preserved"]
    assert result["check"]["sync_version"]==1
    assert len(list(tmp_path.glob("*.pre-sync-v1-*.sqlite")))==1
    with connect(path) as c:
        assert c.execute("SELECT qty FROM binance_trades").fetchone()[0]=="1"
        assert c.execute("SELECT has_trades FROM binance_market_catalog").fetchone()[0]==1
    again=run(path,apply=True)
    assert again["success"]
    assert len(list(tmp_path.glob("*.pre-sync-v1-*.sqlite")))==1


def test_sync_migration_failure_rolls_back_all_new_tables(tmp_path,monkeypatch):
    path=legacy_database(tmp_path/"legacy.sqlite")
    def fail(connection):
        raise MigrationRequired("Synthetic post-DDL validation failure")
    monkeypatch.setattr(sync_migrations,"validate",fail)
    with pytest.raises(MigrationRequired):
        init_db(path,allow_financial_migration=True)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT COUNT(*) FROM binance_trades").fetchone()[0]==1
        assert not c.execute("SELECT name FROM sqlite_master WHERE name='binance_sync_jobs'").fetchone()
        assert not c.execute("SELECT name FROM sqlite_master WHERE name='sync_schema_migrations'").fetchone()
    assert len(list(tmp_path.glob("*.pre-sync-v1-*.sqlite")))==1


def test_schema_cookie_invalidates_cache_after_external_ddl(tmp_path):
    path=tmp_path/"db.sqlite";init_db(path)
    with sqlite3.connect(path) as c:
        c.execute("DROP TABLE binance_job_markets")
    with pytest.raises(MigrationRequired):
        init_db(path)


def test_future_financial_version_is_not_hidden_by_read_cache(tmp_path):
    path=tmp_path/"db.sqlite";init_db(path)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO schema_migrations VALUES(999,'future','synthetic')")
    with pytest.raises(MigrationRequired):
        init_db(path)


def test_future_sync_version_is_not_hidden_by_read_cache(tmp_path):
    path=tmp_path/"db.sqlite";init_db(path)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO sync_schema_migrations VALUES(999,'future','synthetic')")
    with pytest.raises(MigrationRequired):
        init_db(path)
