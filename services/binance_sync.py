"""Read-only account synchronization and conservative portfolio reporting."""
import json
import logging
import os
from pathlib import Path
from typing import Any

import requests
from services.binance_api_budget import budgeted_get

from services.binance_alpha import MARKET_SOURCE_ALPHA, MARKET_SOURCE_SPOT, fetch_alpha_market_data
from services.binance_private_client import BinancePrivateClient, BinanceReadError, get_private_client_status, normalize_balances
from services.binance_trade_history import EXCHANGE, SyncBusy, connect, now, sync_lease, sync_market_history, exchange_for_client
from services.binance_portfolio import decode_position, read_trades, synchronize, _store_balances
from services.config import get_min_active_position_value_usdc
from services.history import DB_PATH, init_db
from services.pnl import calculate_fifo_pnl, decimal_value, split_market, _number
from services.scanner import analyze_symbol_multi
from services.symbols import resolve_market_symbol

QUOTE_ASSET = "USDC"
MIN_POSITION_AMOUNT = 0  # No fixed epsilon: even tiny positive assets are retained.
MIN_ACTIVE_POSITION_VALUE_USDC = get_min_active_position_value_usdc()
MIN_POSITION_VALUE_USDC = MIN_ACTIVE_POSITION_VALUE_USDC
PUBLIC_BASE_URL = "https://api.binance.com"
logger = logging.getLogger(__name__)


def _selected_exchange():
    return "BINANCE_SPOT_TESTNET" if get_private_client_status().get("use_testnet") else EXCHANGE


def _now():
    return now()


def _connect(db_path=DB_PATH):
    init_db(db_path)
    return connect(db_path)


def _row_to_dict(row):
    return decode_position(row)


def _symbol_from_asset(asset):
    return asset_to_symbol(asset)


def asset_to_symbol(asset,quote_asset=QUOTE_ASSET):
    return f"{asset.upper()}{quote_asset.upper()}"


def symbol_exists(symbol):
    # A failed request is not evidence of asset disappearance; orchestration
    # relies on an explicit catalog/snapshot and never closes through this helper.
    try:
        response=budgeted_get(f"{PUBLIC_BASE_URL}/api/v3/ticker/price",
                              params={"symbol":symbol.upper()},timeout=10)
        return response.status_code==200
    except requests.RequestException:
        return False


def get_current_price(symbol):
    try:
        response=budgeted_get(f"{PUBLIC_BASE_URL}/api/v3/ticker/price",
                              params={"symbol":symbol.upper()},timeout=10)
        response.raise_for_status()
        value=decimal_value(response.json()["price"],nonnegative=True)
        return value if value>0 else None
    except (requests.RequestException,KeyError,TypeError,ValueError):
        return None


def sync_balances(client=None,db_path=DB_PATH):
    client=client or BinancePrivateClient()
    if not client.configured:
        return []
    exchange=exchange_for_client(client)
    with sync_lease(db_path,exchange=exchange) as owner:
        balances=normalize_balances(client.get_non_zero_balances())
        from services.binance_trade_history import fence
        with connect(db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            fence(connection,exchange,owner)
            _store_balances(connection,balances,
                {item["asset"]:(decimal_value(item["total_decimal"]),"EXACT")
                 for item in balances if item["asset"]=="USDC"},exchange)
        return balances


def is_active_position_value(current_value_usdc):
    return current_value_usdc is not None and decimal_value(current_value_usdc)>=decimal_value(MIN_ACTIVE_POSITION_VALUE_USDC)


def sync_trades(symbol,client=None,db_path=DB_PATH,**pagination):
    client=client or BinancePrivateClient()
    if not client.configured:
        return []
    pagination.setdefault("collect_trades",True)
    result=sync_market_history(symbol,client,db_path,**pagination)
    return result["trades"]


def get_stored_trades(symbol,db_path=DB_PATH,*,exchange=None):
    exchange=exchange or _selected_exchange()
    with _connect(db_path) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM binance_trades WHERE exchange=? AND symbol=? ORDER BY time,trade_id",
            (exchange,symbol.upper()))]


def rebuild_fifo_position(trades,symbol,current_price=None,current_balance=None,**options):
    result=calculate_fifo_pnl(trades,symbol,current_price,current_balance,**options)
    return {**result,"quantity":result["remaining_quantity"],"remaining_qty":result["remaining_quantity"],
            "average_buy_price":result["average_remaining_cost"],"invested_amount":result["invested_remaining"]}


def estimate_average_buy_price(symbol,db_path=DB_PATH):
    with _connect(db_path) as connection:
        state=connection.execute("SELECT status FROM binance_trade_sync WHERE exchange=? AND symbol=?",(_selected_exchange(),symbol.upper())).fetchone()
    return rebuild_fifo_position(get_stored_trades(symbol,db_path),symbol,
                                 history_complete=bool(state and state["status"]=="COMPLETE"),funding_history_complete=False)


def calculate_realized_pnl(symbol,db_path=DB_PATH):
    return estimate_average_buy_price(symbol,db_path)["realized_pnl"]


def calculate_unrealized_pnl(symbol,current_price=None,db_path=DB_PATH):
    if current_price is None:
        try:
            current_price=analyze_symbol_multi(symbol=symbol,limit=120).get("current_price")
        except Exception:
            current_price=None
    with _connect(db_path) as connection:
        state=connection.execute("SELECT status FROM binance_trade_sync WHERE exchange=? AND symbol=?",(_selected_exchange(),symbol.upper())).fetchone()
    return rebuild_fifo_position(get_stored_trades(symbol,db_path),symbol,current_price=current_price,
                                 history_complete=bool(state and state["status"]=="COMPLETE"),funding_history_complete=False)


def sync_trades_for_symbols(symbols,client=None,db_path=DB_PATH):
    return {symbol.upper():sync_trades(symbol,client,db_path) for symbol in symbols}


def _tradable_balances(balances):
    return [row for row in normalize_balances(balances)
            if row["asset"]!=QUOTE_ASSET and decimal_value(row["total_decimal"])>0]


def rebuild_active_positions_from_balances(balances,client=None,db_path=DB_PATH):
    # Compatibility helper: the caller supplies the authoritative balance list.
    real_client=client or BinancePrivateClient()
    class SnapshotClient:
        configured=real_client.configured
        config=getattr(real_client,"config",None)
        def get_non_zero_balances(self):
            return balances
        def get_spot_markets(self):
            return real_client.get_spot_markets()
        def get_my_trades(self,*args,**kwargs):
            return real_client.get_my_trades(*args,**kwargs)
    result=sync_binance_positions(SnapshotClient(),db_path)
    return result["positions"],result["dust"]


def cleanup_inactive_binance_positions(active_symbols,dust_symbols=None,db_path=DB_PATH):
    # List absence alone does not authorize a closure. The account coordinator
    # handles confirmed zero balances and complete sold-out FIFO histories.
    protected=active_symbols+(dust_symbols or [])
    with _connect(db_path) as connection:
        rows=connection.execute("SELECT id,symbol FROM positions WHERE synced_from_binance=1 AND exchange=?",(_selected_exchange(),)).fetchall()
        for row in rows:
            if row["symbol"] not in protected:
                connection.execute("UPDATE positions SET data_status='TEMPORARILY_UNAVAILABLE',price_status='UNAVAILABLE',pnl_status='INCOMPLETE' WHERE id=?",(row["id"],))


def sync_binance_positions(client=None,db_path=DB_PATH,*,symbols=(),page_size=1000,max_pages=10000,
                          mode="incremental",full_scan=False,progress=None,should_cancel=None,resume_completed=(),lease_owner=None):
    client=client or BinancePrivateClient()
    if not client.configured:
        return {"positions":[],"dust":[],"synced":False,"sync_status":"NOT_CONFIGURED"}
    symbols=tuple(s.strip().upper() if isinstance(s,str) else s for s in symbols)
    extras=tuple(filter(None,(value.strip().upper() for value in os.getenv("CRYPTOAI_HISTORY_SYMBOLS","").split(","))))
    return synchronize(client,db_path,get_price=getattr(client,"get_market_price",get_current_price),resolve=resolve_market_symbol,
        get_alpha=fetch_alpha_market_data,threshold=MIN_ACTIVE_POSITION_VALUE_USDC,
        extra_symbols=tuple(symbols)+(extras if mode=="incremental" else ()),page_size=page_size,max_pages=max_pages,mode=mode,
        full_scan=full_scan,progress=progress,should_cancel=should_cancel,resume_completed=resume_completed,lease_owner=lease_owner)


def sync_positions_from_binance(client=None,db_path=DB_PATH):
    return sync_binance_positions(client,db_path)["positions"]


def sync_binance_account(client=None,db_path=DB_PATH):
    client=client or BinancePrivateClient()
    status={**get_private_client_status(),"configured":client.configured,
            "use_testnet":exchange_for_client(client)=="BINANCE_SPOT_TESTNET"}
    if not client.configured:
        return {**status,"synced":False,"message":"API Binance priv\u00e9e non configur\u00e9e",
                "last_sync":get_last_sync(db_path,exchange=exchange_for_client(client))}
    try:
        result=sync_binance_positions(client,db_path)
        return {**status,**{k:v for k,v in result.items() if k not in {"positions","dust","closed"}},
                "positions_synced":len(result["positions"]),"last_sync":get_last_sync(db_path,exchange=exchange_for_client(client)),
                "active_threshold_usdc":MIN_ACTIVE_POSITION_VALUE_USDC}
    except BinanceReadError as error:
        return {**status,"synced":False,"sync_status":"ERROR",
                "message":"Synchronisation Binance indisponible",
                "retry_after":error.retry_after,
                "last_sync":get_last_sync(db_path,exchange=exchange_for_client(client))}
    except SyncBusy:
        return {**status,"synced":False,"sync_status":"BUSY",
                "message":"Synchronisation Binance d\u00e9j\u00e0 en cours","last_sync":get_last_sync(db_path,exchange=exchange_for_client(client))}


def get_last_sync(db_path=DB_PATH,*,exchange=None):
    with _connect(db_path) as connection:
        row=connection.execute("SELECT last_sync FROM exchange_sync_state WHERE exchange=?",
                               (exchange or _selected_exchange(),)).fetchone()
    return row["last_sync"] if row else None


def get_binance_status(db_path=DB_PATH):
    with _connect(db_path) as connection:
        row=connection.execute("SELECT * FROM exchange_sync_state WHERE exchange=?",
                               (_selected_exchange(),)).fetchone()
    return {**get_private_client_status(),"last_sync":row["last_sync"] if row else None,
            "sync_status":row["sync_status"] if row else "NEVER",
            "discovery_status":row["discovery_status"] if row else "INCOMPLETE",
            "markets_complete":row["markets_complete"] if row else 0,
            "markets_total":row["markets_total"] if row else 0,
            "historical_coverage_as_of":row["coverage_as_of"] if row else None,
            "markets_unrefreshed":row["markets_unrefreshed"] if row else 0,
            "refresh_scope":row["refresh_scope"] if row else "UNKNOWN"}


def get_balances(db_path=DB_PATH):
    with _connect(db_path) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM binance_balances WHERE exchange=? AND total>0 ORDER BY total DESC",(_selected_exchange(),))]


def get_binance_positions(db_path=DB_PATH):
    with _connect(db_path) as connection:
        return [decode_position(row) for row in connection.execute(
            "SELECT * FROM positions WHERE exchange=? AND synced_from_binance=1 AND is_active=1 AND status='ACTIVE' ORDER BY symbol",(_selected_exchange(),))]


def get_active_positions(db_path=DB_PATH):
    with _connect(db_path) as connection:
        return [decode_position(row) for row in connection.execute("""SELECT * FROM positions
            WHERE is_active=1 AND ((synced_from_binance=1 AND exchange=? AND status='ACTIVE')
            OR (COALESCE(synced_from_binance,0)=0 AND source='MANUAL' AND COALESCE(status,'MANUAL') IN ('MANUAL','ACTIVE')))
            ORDER BY symbol""",(_selected_exchange(),))]


def get_inactive_positions(db_path=DB_PATH):
    with _connect(db_path) as connection:
        return [decode_position(row) for row in connection.execute(
            "SELECT * FROM positions WHERE is_active=0 AND (COALESCE(synced_from_binance,0)=0 OR exchange=?) ORDER BY updated_at DESC",(_selected_exchange(),))]


def get_closed_trade_symbols(db_path=DB_PATH):
    with _connect(db_path) as connection:
        return [{"symbol":row[0]} for row in connection.execute(
            "SELECT DISTINCT symbol FROM positions WHERE exchange=? AND synced_from_binance=1 AND status='CLOSED' ORDER BY symbol",(_selected_exchange(),))]


def get_dust_balances(db_path=DB_PATH):
    # Reading the cockpit never performs a fresh market request.
    with _connect(db_path) as connection:
        rows=connection.execute("SELECT * FROM positions WHERE exchange=? AND synced_from_binance=1 AND status='DUST' ORDER BY symbol",(_selected_exchange(),)).fetchall()
    return [{**decode_position(row),"asset":row["binance_asset"],"current_value_usdc":decode_position(row)["current_value"],
             "reason":f"Valeur inf\u00e9rieure au seuil actif de {MIN_ACTIVE_POSITION_VALUE_USDC:g} USDC"}
            for row in rows]


def get_pnl_summary(db_path=DB_PATH):
    from decimal import Decimal, localcontext
    with _connect(db_path) as connection:
        rows=[decode_position(row) for row in connection.execute(
            "SELECT * FROM positions WHERE exchange=? AND synced_from_binance=1 AND data_status!='CASH_BALANCE_ONLY'",(_selected_exchange(),))]
        cash=[dict(row) for row in connection.execute(
            "SELECT * FROM binance_balances WHERE exchange=? AND asset IN ('USDC','USDT','EUR')",(_selected_exchange(),))]
        manual_closed=[dict(row) for row in connection.execute("""SELECT realized_pnl
            FROM positions WHERE COALESCE(synced_from_binance,0)=0 AND source='MANUAL'
            AND status='CLOSED'""")]
        state=connection.execute("SELECT * FROM exchange_sync_state WHERE exchange=?",
                                 (_selected_exchange(),)).fetchone()
    active=[row for row in rows if row["status"]=="ACTIVE"]
    dust=[row for row in rows if row["status"]=="DUST"]
    closed=[row for row in rows if row["status"]=="CLOSED"]
    initialized=bool(state and state["last_attempt"])
    with localcontext() as context:
        context.prec=256
        manual_recorded=Decimal(0)
        manual_unknown=0
        for row in manual_closed:
            try:
                value=decimal_value(row["realized_pnl"])
                # Zero was the historical storage default without a sale record.
                if value==0:
                    manual_unknown+=1
                else:
                    manual_recorded+=value
            except ValueError:
                manual_unknown+=1
        def aggregate(key,selection):
            known=Decimal(0)
            complete=initialized
            if key=="realized_pnl":
                complete=complete and state["sync_status"]=="COMPLETE" and state["discovery_status"]=="COMPLETE" and state["markets_unrefreshed"]==0
            for row in selection:
                if row.get("data_status") in {"LEGACY_UNVERIFIED","TEMPORARILY_UNAVAILABLE"}:
                    complete=False
                    continue
                # Decimal strings are authoritative; floats are compatibility only.
                aliases={"invested_amount":"invested_remaining"}
                exact=row.get("decimal_values",{}).get(aliases.get(key,key))
                value=decimal_value(exact) if exact is not None else None
                if value is None:
                    complete=False
                else:
                    known+=value
            return (known if complete else None),known
        current,known_current=aggregate("current_value",active)
        dust_value,known_dust=aggregate("current_value",dust)
        invested,known_invested=aggregate("invested_amount",[r for r in rows if r["status"]!="CLOSED"])
        unrealized,known_unrealized=aggregate("unrealized_pnl",rows)
        realized,known_realized=aggregate("realized_pnl",rows)
        foreign_cash=sum(row["asset"]!="USDC" for row in cash)
        if foreign_cash:
            unrealized=None
        total=realized+unrealized if realized is not None and unrealized is not None else None
        cash_value=Decimal(0)
        known_cash=Decimal(0)
        cash_complete=initialized
        usdc=Decimal(0)
        for row in cash:
            quantity=decimal_value(row.get("total_decimal") or row["total"],nonnegative=True)
            if row["asset"]=="USDC":
                usdc+=quantity
                value=quantity
            else:
                value=None if row["value_usdc"] is None else decimal_value(row["value_usdc"])
            if value is None:
                cash_complete=False
            else:
                known_cash+=value
                cash_value+=value
        wallet_values=[r for r in rows if r["status"]!="CLOSED"]
        wallet,known_wallet=aggregate("current_value",wallet_values)
        portfolio=wallet+cash_value if wallet is not None and cash_complete else None
        values={"total_invested":invested,"current_value":current,
                "usdc_available":usdc if initialized else None,
                "portfolio_value_usdc":portfolio,"portfolio_value_eur":None,
                "usdc_to_eur_rate_estimate":None,"unrealized_pnl":unrealized,
                "realized_pnl":realized,"total_pnl":total,
                "dust_value_usdc":dust_value,"known_current_value":known_current,
                "known_portfolio_value_usdc":known_wallet+known_cash,
                "known_unrealized_pnl":known_unrealized,"known_realized_pnl":known_realized,
                "known_invested_amount":known_invested}
        return {**{key:_number(value) for key,value in values.items()},
                "decimal_values":{key:str(value) if value is not None else None for key,value in values.items()},
                "manual_closed_positions_count":len(manual_closed),
                "manual_recorded_results_count":len(manual_closed)-manual_unknown,
                "manual_realized_pnl_recorded":_number(manual_recorded) if manual_closed and not manual_unknown else None,
                "known_manual_realized_pnl_recorded":_number(manual_recorded),
                "manual_realized_pnl_status":"INCOMPLETE" if manual_unknown or not manual_closed else "ESTIMATED",
                "quote_asset":"USDC","scope":_selected_exchange(),
                "cash_fx_cost_unknown_count":foreign_cash,
                "coverage_scope":"AVAILABLE_AND_STORED_SPOT_MARKETS_USDC_USDT_EUR_BTC",
                "funding_history_status":"NOT_DOCUMENTED",
                "historical_coverage_as_of":state["coverage_as_of"] if state else None,
                "markets_unrefreshed":state["markets_unrefreshed"] if state else 0,
                "refresh_scope":state["refresh_scope"] if state else "UNKNOWN",
                "pnl_status":("EXACT" if all(row.get("pnl_status")=="EXACT" for row in rows) else "ESTIMATED") if total is not None else "INCOMPLETE",
                "valuation_status":"EXACT" if portfolio is not None and all(r["asset"]=="USDC" for r in cash) and all(r.get("price_status")=="EXACT" for r in wallet_values) else "ESTIMATED" if portfolio is not None else "INCOMPLETE",
                "is_estimated":not initialized or bool(foreign_cash) or total is None or any(row.get("pnl_status")!="EXACT" for row in rows),
                "incomplete_positions_count":sum(row.get("pnl_status") not in {"EXACT","ESTIMATED"} for row in rows),
                "active_positions_count":len(active),"inactive_symbols_count":len(closed),
                "closed_positions_count":len(closed),"dust_count":len(dust),
                "active_threshold_usdc":MIN_ACTIVE_POSITION_VALUE_USDC,
                "sync_status":state["sync_status"] if state else "NEVER",
                "valuation_as_of":state["last_attempt"] if state else None}


def get_account_summary(db_path=DB_PATH):
    pnl=get_pnl_summary(db_path)
    return {"active_value_usdc":pnl["current_value"],"dust_value_usdc":pnl["dust_value_usdc"],
            "active_positions_count":pnl["active_positions_count"],"dust_count":pnl["dust_count"],
            "closed_positions_count":pnl["closed_positions_count"],"valuation_status":pnl["valuation_status"],
            "active_threshold_usdc":MIN_ACTIVE_POSITION_VALUE_USDC}
