"""Durable trade pagination and a cross-process, fenced SQLite sync lease."""
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import time
import uuid

from services.binance_private_client import BinanceReadError, normalize_trade
from services.history import init_db
from services.pnl import decimal_value, split_market

EXCHANGE = "BINANCE_SPOT"


def exchange_for_client(client):
    return "BINANCE_SPOT_TESTNET" if getattr(getattr(client,"config",None),"use_testnet",False) else EXCHANGE


class SyncBusy(RuntimeError):
    pass


class SyncCancelled(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


def now():
    return datetime.now(UTC).isoformat()


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path):
    connection = sqlite3.connect(path,timeout=10,factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    return connection


def fence(connection, exchange, owner, *, clock=time.time):
    updated = connection.execute(
        "UPDATE financial_sync_lock SET expires_at=? WHERE exchange=? AND owner=? AND expires_at>?",
        (clock()+90,exchange,owner,clock()))
    if updated.rowcount != 1:
        raise LeaseLost("Financial synchronization lease expired")


@contextmanager
def sync_lease(path, *, exchange=EXCHANGE, clock=time.time, owner=None):
    init_db(path)
    owner = owner or uuid.uuid4().hex
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT expires_at FROM financial_sync_lock WHERE exchange=?",(exchange,)).fetchone()
        if existing and existing["expires_at"] > clock():
            raise SyncBusy("Financial synchronization already running")
        connection.execute(
            "INSERT INTO financial_sync_lock VALUES(?,?,?) ON CONFLICT(exchange) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at",
            (exchange,owner,clock()+90))
    try:
        yield owner
    finally:
        with connect(path) as connection:
            connection.execute("DELETE FROM financial_sync_lock WHERE exchange=? AND owner=?",
                               (exchange,owner))


def _state(connection, exchange, symbol, *, next_id=0, complete=False, status="PENDING", error=None):
    connection.execute("""INSERT INTO binance_trade_sync
        (exchange,symbol,next_id,initial_complete,status,updated_at,error_code)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(exchange,symbol) DO UPDATE SET
        next_id=excluded.next_id,initial_complete=excluded.initial_complete,
        status=excluded.status,updated_at=excluded.updated_at,error_code=excluded.error_code""",
        (exchange,symbol,next_id,int(complete),status,now(),error))


def _same_trade(existing, trade):
    for field in ("order_id","side","commission_asset","time","is_buyer","is_maker"):
        if existing[field] != trade[field]:
            if existing["precision_status"]=="LEGACY_FLOAT" and existing[field] is None:
                continue
            return False
    for field in ("price","qty","quote_qty","commission"):
        if existing["precision_status"]=="LEGACY_FLOAT" and existing[field] is None and field=="commission":
            continue
        old, new = decimal_value(existing[field]), decimal_value(trade[field])
        if old != new and (existing["precision_status"] != "LEGACY_FLOAT"
                          or abs(old-new) > max(abs(old),abs(new))*decimal_value("1e-12")):
            return False
    return True


def store_page(connection, trades, *, exchange, base_asset, quote_asset):
    inserted = 0
    fields = ("symbol","order_id","trade_id","side","price","qty","quote_qty",
              "commission","commission_asset","time","is_buyer","is_maker")
    for trade in trades:
        existing = connection.execute(
            "SELECT * FROM binance_trades WHERE exchange=? AND symbol=? AND trade_id=?",
            (exchange,trade["symbol"],trade["trade_id"])).fetchone()
        if existing:
            if not _same_trade(existing,trade):
                raise BinanceReadError("CONFLICTING_TRADE")
            if existing["precision_status"] == "LEGACY_FLOAT":
                connection.execute("UPDATE binance_trades SET "+
                    ",".join(name+"=?" for name in fields)+
                    ",base_asset=?,quote_asset=?,precision_status='NATIVE_DECIMAL' WHERE id=?",
                    (*(trade[name] for name in fields),base_asset,quote_asset,existing["id"]))
            continue
        connection.execute(
            "INSERT INTO binance_trades ("+",".join(fields)+
            ",exchange,base_asset,quote_asset,precision_status) VALUES("+
            ",".join("?" for _ in range(len(fields)+4))+")",
            (*(trade[name] for name in fields),exchange,base_asset,quote_asset,"NATIVE_DECIMAL"))
        inserted += 1
    return inserted


def sync_market_history(symbol, client, db_path, *, owner=None, exchange=None,
                        base_asset=None, quote_asset=None, page_size=1000, max_pages=10000, collect_trades=False, checkpoint=None, progress=None):
    exchange=exchange or exchange_for_client(client)
    if owner is None:
        with sync_lease(db_path,exchange=exchange) as lease:
            return sync_market_history(symbol,client,db_path,owner=lease,exchange=exchange,
                base_asset=base_asset,quote_asset=quote_asset,page_size=page_size,max_pages=max_pages,collect_trades=collect_trades,checkpoint=checkpoint,progress=progress)
    symbol = symbol.upper()
    inferred_base,inferred_quote = split_market(symbol)
    base_asset,quote_asset = base_asset or inferred_base,quote_asset or inferred_quote
    if quote_asset is None:
        raise BinanceReadError("UNSUPPORTED_MARKET")
    if not 1 <= page_size <= 1000 or not 1 <= max_pages <= 10000:
        raise ValueError("Invalid pagination budget")
    with connect(db_path) as connection:
        state = connection.execute(
            "SELECT * FROM binance_trade_sync WHERE exchange=? AND symbol=?",
            (exchange,symbol)).fetchone()
    cursor = state["next_id"] if state else 0
    initial_complete = bool(state and state["initial_complete"])
    pages, inserted, received = 0,0,[]
    try:
        for _ in range(max_pages):
            if checkpoint:
                checkpoint()
            with connect(db_path) as connection:
                fence(connection,exchange,owner)
            raw = client.get_my_trades(symbol,from_id=cursor,limit=page_size)
            if checkpoint:
                checkpoint()
            if not isinstance(raw,list) or len(raw)>page_size:
                raise BinanceReadError("INVALID_PAGE")
            trades = [normalize_trade(trade,symbol) for trade in raw]
            pages += 1
            if not trades:
                with connect(db_path) as connection:
                    fence(connection,exchange,owner)
                    _state(connection,exchange,symbol,next_id=cursor,complete=True,status="COMPLETE")
                    if progress:
                        progress({"event":"PAGE","symbol":symbol,"inserted":0,"duplicates":0,"pages":1},connection=connection)
                return {"symbol":symbol,"complete":True,"pages":pages,
                        "inserted":inserted,"next_id":cursor,"trades":received}
            next_cursor = max(trade["trade_id"] for trade in trades)+1
            if next_cursor <= cursor or next_cursor > 9223372036854775807:
                raise BinanceReadError("PAGINATION_NO_PROGRESS")
            with connect(db_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                fence(connection,exchange,owner)
                # Reject unseen historical entries from a broken/overlapping cursor.
                for trade in trades:
                    if trade["trade_id"] < cursor and not connection.execute(
                        "SELECT 1 FROM binance_trades WHERE exchange=? AND symbol=? AND trade_id=?",
                        (exchange,symbol,trade["trade_id"])).fetchone():
                        raise BinanceReadError("INVALID_OVERLAP")
                added = store_page(connection,trades,exchange=exchange,
                                       base_asset=base_asset,quote_asset=quote_asset)
                inserted += added
                if progress:
                    progress({"event":"PAGE","symbol":symbol,"inserted":added,"duplicates":len(trades)-added,"pages":1},connection=connection)
                _state(connection,exchange,symbol,next_id=next_cursor,
                       complete=initial_complete,status="PAGINATING")
            cursor = next_cursor
            if collect_trades:
                received.extend(raw)
        raise BinanceReadError("PAGINATION_BUDGET_EXCEEDED")
    except Exception as error:
        # Save resumable progress, but never raw exception/request contents.
        code = error.code if isinstance(error,BinanceReadError) else type(error).__name__
        with connect(db_path) as connection:
            fence(connection,exchange,owner)
            _state(connection,exchange,symbol,next_id=cursor,
                   complete=initial_complete,status="ERROR",error=code)
        raise
