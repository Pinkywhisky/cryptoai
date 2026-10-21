"""Versioned sync metadata, added only on new databases or explicitly approved copies."""
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
import os
import sqlite3
import uuid

from services.financial_migrations import MigrationRequired, _is_personal, _version

SYNC_SCHEMA_VERSION = 1
TABLES = {
    "binance_market_catalog", "binance_catalog_state", "binance_sync_jobs",
    "binance_job_markets", "binance_sync_previews", "binance_api_state",
}


def version(connection):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='sync_schema_migrations' AND type='table'").fetchone():
        return 0
    return connection.execute("SELECT COALESCE(MAX(version),0) FROM sync_schema_migrations").fetchone()[0]


def validate(connection):
    if version(connection) != SYNC_SCHEMA_VERSION:
        raise MigrationRequired("Synchronization schema migration required")
    required={
        "binance_market_catalog":{"exchange","symbol","market_status","last_attempt","last_success","next_review","last_error","has_trades"},
        "binance_catalog_state":{"exchange","updated_at","payload_json"},
        "binance_sync_jobs":{"id","exchange","mode","status","owner","cancel_requested","processed","total","inserted","duplicates","pages","updated_at","result_json"},
        "binance_job_markets":{"job_id","symbol","status","error_code"},
        "binance_sync_previews":{"id","exchange","created_at","full_scan","payload_json"},
        "binance_api_state":{"network","payload_json"},
        "exchange_sync_state":{"coverage_as_of","markets_unrefreshed","refresh_scope"},
    }
    for table,columns in required.items():
        actual={row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        if not columns<=actual:
            raise MigrationRequired("Incomplete synchronization schema")
    index=connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='binance_one_active_job'").fetchone()
    if not index or not index[0] or "UNIQUE INDEX" not in index[0].upper():
        raise MigrationRequired("Missing single synchronization job constraint")


def prepare(path, *, allow_existing=False):
    path=Path(path)
    if not path.exists() or not path.stat().st_size:
        return
    if path.is_symlink() or path.stat().st_nlink != 1:
        raise MigrationRequired("Isolated regular database required")
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)) as source:
        current=version(source)
        if current > SYNC_SCHEMA_VERSION:
            raise MigrationRequired("Synchronization schema is newer than this application")
        if current == SYNC_SCHEMA_VERSION:
            validate(source)
            return
        # Financial v0 is already backed up by the financial migration.
        if _version(source)==0:
            return
        if _is_personal(path) or not allow_existing:
            raise MigrationRequired("Synchronization migration requires explicit opt-in on an isolated copy")
        backup=path.with_name(path.name+".pre-sync-v1-"+uuid.uuid4().hex+".sqlite")
        fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.close(fd)
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise MigrationRequired("Synchronization backup verification failed")


def apply(connection):
    current=version(connection)
    if current==SYNC_SCHEMA_VERSION:
        validate(connection)
        return
    if current or not connection.in_transaction:
        raise MigrationRequired("Explicit synchronization migration transaction required")
    database=connection.execute("PRAGMA database_list").fetchone()[2]
    if database and _is_personal(database):
        raise MigrationRequired("Personal database migration is forbidden")
    statements=[
        """CREATE TABLE binance_market_catalog(
            exchange TEXT NOT NULL,symbol TEXT NOT NULL,base_asset TEXT NOT NULL,quote_asset TEXT NOT NULL,
            market_status TEXT NOT NULL DEFAULT 'UNKNOWN',catalog_at REAL NOT NULL DEFAULT 0,
            last_attempt REAL,last_success REAL,next_review REAL NOT NULL DEFAULT 0,
            last_error TEXT,has_trades INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(exchange,symbol))""",
        """CREATE TABLE binance_catalog_state(
            exchange TEXT PRIMARY KEY,updated_at REAL NOT NULL,payload_json TEXT NOT NULL)""",
        """CREATE TABLE binance_sync_jobs(
            id TEXT PRIMARY KEY,exchange TEXT NOT NULL,mode TEXT NOT NULL,scope TEXT NOT NULL,
            status TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,
            owner TEXT,cancel_requested INTEGER NOT NULL DEFAULT 0,
            processed INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,
            inserted INTEGER NOT NULL DEFAULT 0,duplicates INTEGER NOT NULL DEFAULT 0,
            pages INTEGER NOT NULL DEFAULT 0,error_code TEXT,retry_at REAL,
            result_json TEXT,attempt INTEGER NOT NULL DEFAULT 0)""",
        """CREATE UNIQUE INDEX binance_one_active_job ON binance_sync_jobs((1))
            WHERE status IN ('QUEUED','RUNNING','CANCELLING')""",
        """CREATE INDEX binance_job_chronology ON binance_sync_jobs(exchange,created_at DESC)""",
        """CREATE TABLE binance_job_markets(
            job_id TEXT NOT NULL,symbol TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING',
            error_code TEXT,updated_at REAL,PRIMARY KEY(job_id,symbol),
            FOREIGN KEY(job_id) REFERENCES binance_sync_jobs(id))""",
        """CREATE TABLE binance_sync_previews(
            id TEXT PRIMARY KEY,exchange TEXT NOT NULL,created_at REAL NOT NULL,
            full_scan INTEGER NOT NULL,payload_json TEXT NOT NULL)""",
        """CREATE TABLE binance_api_state(
            network TEXT PRIMARY KEY,payload_json TEXT NOT NULL)""",
        """CREATE TABLE sync_schema_migrations(
            version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL)""",
    ]
    for statement in statements:
        connection.execute(statement)
    for name,definition in [("coverage_as_of","TEXT"),("markets_unrefreshed","INTEGER NOT NULL DEFAULT 0"),
                            ("refresh_scope","TEXT NOT NULL DEFAULT 'UNKNOWN'")]:
        connection.execute(f'ALTER TABLE exchange_sync_state ADD COLUMN "{name}" {definition}')
    # Seed only markets actually known locally, never pretend all catalog markets were used.
    connection.execute("""INSERT INTO binance_market_catalog(exchange,symbol,base_asset,quote_asset,
            market_status,has_trades)
        SELECT exchange,symbol,COALESCE(base_asset,''),COALESCE(quote_asset,''),'UNKNOWN',1
        FROM binance_trades GROUP BY exchange,symbol""")
    connection.execute("INSERT INTO sync_schema_migrations VALUES(?,?,?)",
                       (SYNC_SCHEMA_VERSION,"incremental synchronization",datetime.now(UTC).isoformat()))
    validate(connection)
