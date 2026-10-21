"""Catalog cache, explicit bootstrap plans, and conservative incremental selection."""
import hashlib
import json
import math
import time
import uuid

from services.binance_private_client import ASSET, BinanceDataError, BinanceReadError
from services.binance_trade_history import connect
from services.history import init_db
from services.pnl import SUPPORTED_QUOTES, split_market

CATALOG_TTL=6*3600
REVIEW_DELAY=6*3600


def validate_catalog(catalog):
    if not isinstance(catalog,list) or not catalog:
        raise BinanceDataError("INVALID_MARKET_CATALOG")
    seen=set()
    rows=[]
    for row in catalog:
        if not isinstance(row,dict) or not all(isinstance(row.get(k),str) and ASSET.fullmatch(row[k])
                for k in ("symbol","base_asset","quote_asset")):
            raise BinanceDataError("INVALID_MARKET_CATALOG")
        if row["symbol"]!=row["base_asset"]+row["quote_asset"] or row["symbol"] in seen:
            raise BinanceDataError("INVALID_MARKET_CATALOG")
        seen.add(row["symbol"])
        status=row.get("status","UNKNOWN")
        if not isinstance(status,str) or not ASSET.fullmatch(status):
            status="UNKNOWN"
        rows.append({k:row[k] for k in ("symbol","base_asset","quote_asset")} | {"status":status})
    return rows


def load_catalog(client,path,exchange,*,force=False,clock=time.time):
    init_db(path)
    now=clock()
    with connect(path) as connection:
        saved=connection.execute("SELECT * FROM binance_catalog_state WHERE exchange=?",(exchange,)).fetchone()
    if saved and not force and 0<=now-saved["updated_at"]<CATALOG_TTL:
        payload=json.loads(saved["payload_json"])
        budget=getattr(client,"budget",None)
        if budget and payload.get("rate_limits"):
            from services.binance_api_budget import network_for
            budget.publish_limits(network_for(client.base_url),payload["rate_limits"],
                                  observed_at=saved["updated_at"],from_cache=True)
        return validate_catalog(payload["markets"]),True,False
    try:
        rows=validate_catalog(client.get_spot_markets())
    except (BinanceReadError,ValueError,AttributeError) as error:
        if isinstance(error,BinanceReadError) and error.code in {"RATE_LIMIT","LOCAL_QUOTA","HTTP_418","HTTP_401","HTTP_403","SERVER","TRANSPORT","RETRY_LATER"}:
            raise
        if saved:
            return validate_catalog(json.loads(saved["payload_json"])["markets"]),False,False
        raise
    payload={"markets":rows,"rate_limits":getattr(client,"catalog_rate_limits",None)}
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        # A missing catalog entry is unrecognized, never erased or called delisted.
        connection.execute("UPDATE binance_market_catalog SET market_status='UNRECOGNIZED' WHERE exchange=?",(exchange,))
        for row in rows:
            connection.execute("""INSERT INTO binance_market_catalog
                (exchange,symbol,base_asset,quote_asset,market_status,catalog_at) VALUES(?,?,?,?,?,?)
                ON CONFLICT(exchange,symbol) DO UPDATE SET base_asset=excluded.base_asset,
                quote_asset=excluded.quote_asset,market_status=excluded.market_status,catalog_at=excluded.catalog_at,
                next_review=CASE WHEN binance_market_catalog.market_status!=excluded.market_status
                    THEN 0 ELSE binance_market_catalog.next_review END""",
                (exchange,row["symbol"],row["base_asset"],row["quote_asset"],row["status"],now))
        connection.execute("""INSERT INTO binance_catalog_state VALUES(?,?,?)
            ON CONFLICT(exchange) DO UPDATE SET updated_at=excluded.updated_at,payload_json=excluded.payload_json""",
            (exchange,now,json.dumps(payload)))
    return rows,True,True


def estimate_cost(markets, *, trade_counts=None,page_size=1000,account_calls=2,
                  catalog_calls=0,price_calls=0,weight_limit=6000,safety=.8,weight_interval_seconds=60):
    if not 1<=page_size<=1000 or weight_limit<=0 or weight_interval_seconds<=0 or not 0<safety<1:
        raise ValueError("Invalid simulation parameters")
    counts=trade_counts or {}
    pages=sum(math.ceil(counts.get(symbol,0)/page_size)+1 for symbol in markets)
    weight=pages*20+account_calls*20+catalog_calls*20+price_calls*2
    return {"markets":len(markets),"private_requests_min":len(markets)+account_calls,
            "private_requests_scenario":pages+account_calls,"my_trades_pages":pages,
            "estimated_weight":weight,"public_requests":catalog_calls+price_calls,
            "weight_limit_assumed":weight_limit,"weight_interval_seconds":weight_interval_seconds,"safety":safety,
            "estimated_average_budget_seconds":weight/(weight_limit*safety)*weight_interval_seconds,
            "estimate_only":True,"pages_assumption":"provided trade counts plus one terminal empty page; unknown counts assumed zero",
            "historical_pages_unbounded_by_preview":True}


def known_symbols(connection,exchange):
    used={row[0] for row in connection.execute("SELECT DISTINCT symbol FROM binance_trades WHERE exchange=?",(exchange,))}
    used|={row[0] for row in connection.execute("""SELECT symbol FROM positions
        WHERE exchange=? AND synced_from_binance=1 AND COALESCE(market_source,'UNKNOWN')!='BINANCE_ALPHA'""",(exchange,))}
    return used


def select_markets(path,exchange,markets,balances,*,mode="incremental",symbols=(),full_scan=False,clock=time.time):
    if mode not in {"incremental","bootstrap"} or (full_scan and mode!="bootstrap"):
        raise ValueError("Invalid synchronization mode")
    explicit=set(symbols)
    if any(not isinstance(s,str) or not ASSET.fullmatch(s) for s in explicit):
        raise BinanceDataError("INVALID_HISTORY_SYMBOL")
    now=clock()
    with connect(path) as connection:
        known=known_symbols(connection,exchange)
        fees={row[0] for row in connection.execute("""SELECT DISTINCT commission_asset FROM binance_trades
                WHERE exchange=? AND CAST(commission AS REAL)>0""",(exchange,))}
        registry={row["symbol"]:dict(row) for row in connection.execute("SELECT * FROM binance_market_catalog WHERE exchange=?",(exchange,))}
    held={row["asset"] for row in balances}
    related={s for s,row in markets.items() if row["base_asset"] in held|fees}
    candidates=(set(markets) if full_scan else explicit if mode=="bootstrap" and explicit else explicit|known|related)
    selected={}
    deferred=[]
    for symbol in sorted(candidates):
        if symbol not in markets:
            base,quote=split_market(symbol)
            if quote:
                markets[symbol]={"symbol":symbol,"base_asset":base,"quote_asset":quote,"status":"UNRECOGNIZED"}
            else:
                continue
        record=registry.get(symbol,{})
        # Explicit bootstrap is allowed to retry a historical market immediately.
        if mode=="incremental" and symbol not in explicit and record.get("next_review",0)>now:
            deferred.append(symbol)
        else:
            selected[symbol]=markets[symbol]
    return {"selected":selected,"deferred":deferred,"known":sorted(known),
            "scope":"CATALOG" if full_scan else "TARGETED" if mode=="bootstrap" else "INCREMENTAL"}


def record_market(path,exchange,symbol,*,success,inserted=0,error=None,clock=time.time):
    base,quote=split_market(symbol)
    now=clock()
    with connect(path) as connection:
        connection.execute("""INSERT INTO binance_market_catalog(exchange,symbol,base_asset,quote_asset)
            VALUES(?,?,?,?) ON CONFLICT(exchange,symbol) DO NOTHING""",(exchange,symbol,base,quote or ""))
        row=connection.execute("SELECT market_status FROM binance_market_catalog WHERE exchange=? AND symbol=?",(exchange,symbol)).fetchone()
        status=row["market_status"]
        if error=="UNKNOWN_SYMBOL":
            status="UNRECOGNIZED"
        delay=(0 if error in {"RATE_LIMIT","LOCAL_QUOTA","HTTP_418","RETRY_LATER"} else
           REVIEW_DELAY if status not in {"TRADING","UNKNOWN"} or error=="UNKNOWN_SYMBOL" else
           60 if not success else 0)
        connection.execute("""UPDATE binance_market_catalog SET last_attempt=?,last_success=CASE WHEN ? THEN ? ELSE last_success END,
            next_review=?,last_error=?,has_trades=MAX(has_trades,?),market_status=? WHERE exchange=? AND symbol=?""",
            (now,int(success),now,now+delay if delay else 0,error,int(inserted>0),status,exchange,symbol))


def coverage(path,exchange,markets):
    with connect(path) as connection:
        states={row["symbol"]:dict(row) for row in connection.execute("SELECT * FROM binance_trade_sync WHERE exchange=?",(exchange,))}
        registry={row["symbol"]:dict(row) for row in connection.execute("SELECT * FROM binance_market_catalog WHERE exchange=?",(exchange,))}
    covered={s for s,row in states.items() if row["initial_complete"] and row["status"]=="COMPLETE"
             and not registry.get(s,{}).get("last_error")}
    unknown=set(markets)-{s for s,row in states.items() if row["initial_complete"]}
    stale={s for s,row in registry.items() if row.get("last_error")}
    dates=[row["updated_at"] for s,row in states.items() if s in covered and row["updated_at"]]
    return {"covered":covered,"unknown":unknown,"stale":stale,"as_of":min(dates) if dates else None}


def preview(client,path,exchange,*,symbols=(),full_scan=False,clock=time.time):
    catalog,fresh,fetched=load_catalog(client,path,exchange)
    if not fresh:
        raise BinanceReadError("STALE_CATALOG")
    markets={row["symbol"]:row for row in catalog if row["quote_asset"] in SUPPORTED_QUOTES}
    with connect(path) as connection:
        stored=known_symbols(connection,exchange)
        balances=[dict(row) for row in connection.execute("SELECT asset FROM binance_balances WHERE exchange=?",(exchange,))]
    requested=set(symbols)
    for symbol in requested|stored:
        if symbol not in markets:
            base,quote=split_market(symbol)
            if not quote or not ASSET.fullmatch(symbol):
                raise BinanceDataError("INVALID_HISTORY_SYMBOL")
            markets[symbol]={"symbol":symbol,"base_asset":base,"quote_asset":quote,"status":"UNRECOGNIZED"}
    plan=select_markets(path,exchange,markets,balances,mode="bootstrap",symbols=requested,full_scan=full_scan,clock=clock)
    selected=sorted(plan["selected"])
    cov=coverage(path,exchange,markets)
    identifier=uuid.uuid4().hex
    from services.binance_api_budget import ApiBudget, network_for
    budget=getattr(client,"budget",None) or ApiBudget(path)
    metrics=budget.metrics(network_for(getattr(client,"base_url","https://api.binance.com")))
    limiting=min((item for item in metrics["limits"] if item["metric"]=="REQUEST_WEIGHT"),
                 key=lambda item:item["limit"]/item["seconds"])
    cost=estimate_cost(selected,catalog_calls=int(fetched),weight_limit=limiting["limit"],
                       weight_interval_seconds=limiting["seconds"],safety=metrics["safety"])
    body={"id":identifier,"symbols":selected,"scope":plan["scope"],"full_scan":bool(full_scan),
          "catalog_digest":hashlib.sha256(json.dumps(sorted(markets),separators=(",",":")).encode()).hexdigest(),
          "catalog_markets":len(markets),"unexplored_markets":len(cov["unknown"]),
          "unknown_symbols":sorted(cov["unknown"])[:100],"unknown_list_truncated":len(cov["unknown"])>100,
          "cost":cost,
          "requires_confirmation":bool(full_scan),"expires_at":clock()+900,
          "coverage_warning":"Current holdings cannot establish all historically traded markets. Scope is explicit; funding is not documented."}
    with connect(path) as connection:
        connection.execute("DELETE FROM binance_sync_previews WHERE created_at<?",(clock()-900,))
        connection.execute("INSERT INTO binance_sync_previews VALUES(?,?,?,?,?)",
                           (identifier,exchange,clock(),int(full_scan),json.dumps(body)))
    return body


def consume_preview(connection,identifier,exchange,*,confirmed=False,clock=time.time):
    row=connection.execute("SELECT * FROM binance_sync_previews WHERE id=? AND exchange=?",(identifier,exchange)).fetchone()
    if not row or not 0<=clock()-row["created_at"]<=900:
        raise ValueError("PREVIEW_EXPIRED_OR_UNKNOWN")
    if row["full_scan"] and not confirmed:
        raise ValueError("FULL_BOOTSTRAP_CONFIRMATION_REQUIRED")
    payload=json.loads(row["payload_json"])
    if not payload["symbols"]:
        raise ValueError("EXPLICIT_MARKETS_REQUIRED")
    connection.execute("DELETE FROM binance_sync_previews WHERE id=?",(identifier,))
    return payload
