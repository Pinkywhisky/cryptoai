"""Explicit financial migration/check for an isolated SQLite copy. Never the personal DB."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services import financial_migrations, sync_migrations
from services.history import init_db

TABLES=("positions","binance_trades","binance_balances","binance_sync_state")


def inspect_copy(path):
    uri=path.resolve().as_uri()+"?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri,uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        names={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts={name:connection.execute('SELECT COUNT(*) FROM "'+name+'"').fetchone()[0]
                for name in TABLES if name in names}
        version=financial_migrations._version(connection)
        sync_version=sync_migrations.version(connection)
        ready=version==financial_migrations.FINANCIAL_SCHEMA_VERSION and sync_version==sync_migrations.SYNC_SCHEMA_VERSION
        if ready:
            financial_migrations.validate_financial_schema(connection)
            sync_migrations.validate(connection)
        integrity=connection.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        foreign_keys=not connection.execute("PRAGMA foreign_key_check").fetchall()
        return {"version":version,"sync_version":sync_version,"ready":ready,"row_counts":counts,
                "integrity_ok":integrity,"foreign_keys_ok":foreign_keys}


def run(path, *, apply=False):
    path=Path(path)
    if financial_migrations._is_personal(path):
        return {"success":False,"error":"PERSONAL_DATABASE_FORBIDDEN"}
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink!=1:
        return {"success":False,"error":"ISOLATED_REGULAR_COPY_REQUIRED"}
    if any(Path(str(path)+suffix).exists() for suffix in ("-wal","-shm","-journal")):
        return {"success":False,"error":"SQLITE_SIDECAR_PRESENT_DO_NOT_DELETE_IT"}
    try:
        before=inspect_copy(path)
        if not before["integrity_ok"] or not before["foreign_keys_ok"]:
            return {"success":False,"error":"INPUT_INTEGRITY_FAILED"}
        if not apply:
            return {"success":before["ready"],"mode":"READ_ONLY","check":before,
                    "error":None if before["ready"] else "MIGRATION_REQUIRED"}
        init_db(path,allow_financial_migration=True)
        after=inspect_copy(path)
        unchanged=all(after["row_counts"].get(table)==count for table,count in before["row_counts"].items())
        return {"success":after["ready"] and after["integrity_ok"] and after["foreign_keys_ok"] and unchanged,
                "mode":"EXPLICIT_ISOLATED_COPY_MIGRATION","historical_counts_preserved":unchanged,
                "check":after,"backups_retained":len(list(path.parent.glob(path.name+".pre-finance-v1-*.sqlite")))+len(list(path.parent.glob(path.name+".pre-sync-v1-*.sqlite")))}
    except Exception as error:
        # Raw SQLite errors can contain private values; only emit a class.
        return {"success":False,"error":"MIGRATION_OR_CHECK_FAILED",
                "error_type":type(error).__name__,"automatic_restore":False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,required=True)
    actions=parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check",action="store_true")
    actions.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    result=run(args.database,apply=args.apply)
    print(json.dumps(result,sort_keys=True))
    return 0 if result["success"] else 1


if __name__=="__main__":
    raise SystemExit(main())
