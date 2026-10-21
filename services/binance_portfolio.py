"""Coordinate source collection, per-asset accounting, and an atomic wallet snapshot."""
from datetime import UTC, datetime
from decimal import Decimal, localcontext
import json
import sqlite3
from contextlib import contextmanager

from services.binance_private_client import (
    ASSET, BinanceReadError, BinanceDataError, normalize_balances)
from services.binance_trade_history import (
    EXCHANGE, LeaseLost, connect, fence, now, sync_lease, sync_market_history, exchange_for_client)
from services.pnl import SUPPORTED_QUOTES, calculate_fifo_pnl, decimal_value, split_market


CIRCUIT_ERRORS={"RATE_LIMIT","RETRY_LATER","SERVER","TRANSPORT",
                "HTTP_401","HTTP_403","HTTP_418"}


def decode_position(row):
    item = dict(row)
    if not item.get("synced_from_binance"):
        return item
    raw = item.get("accounting_json")
    if raw:
        try:
            accounting = json.loads(raw)
        except (ValueError,TypeError):
            accounting = None
        if isinstance(accounting,dict) and isinstance(accounting.get("decimal_values"),dict):
            aliases = {"quantity":"remaining_quantity","average_buy_price":"average_remaining_cost",
                       "invested_amount":"invested_remaining","profit_loss_pct":"unrealized_pnl_pct"}
            for field in ("quantity","average_buy_price","invested_amount","current_price",
                          "current_value","realized_pnl","unrealized_pnl","total_pnl","profit_loss_pct"):
                value = accounting.get(aliases.get(field,field))
                if field in {"current_price","current_value"} and value is None:
                    item["last_known_"+field] = item.get(field)
                item[field] = value
            item["decimal_values"] = accounting["decimal_values"]
            item["issues"] = accounting.get("issues",[])
            item["funding_history_status"]=accounting.get("funding_history_status","NOT_DOCUMENTED")
            item["known_realized_pnl"] = accounting.get("known_realized_pnl")
            if item.get("data_status")=="TEMPORARILY_UNAVAILABLE":
                for field in ("current_price","current_value","unrealized_pnl","total_pnl","profit_loss_pct"):
                    item.setdefault("last_known_"+field,item.get(field))
                    item[field]=None
                item["issues"]=sorted(set(item["issues"])|{"TEMPORARILY_UNAVAILABLE"})
            return item
    # Pre-lot-3 figures are unverified, never silently relabelled exact.
    for field in ("average_buy_price","invested_amount","realized_pnl","unrealized_pnl","total_pnl","profit_loss_pct"):
        item["last_known_"+field] = item.get(field)
        item[field] = None
    item["issues"] = ["ACCOUNTING_SNAPSHOT_INVALID" if raw else "LEGACY_UNVERIFIED"]
    unavailable=item.get("data_status")=="TEMPORARILY_UNAVAILABLE"
    if unavailable:
        for field in ("current_price","current_value"):
            item["last_known_"+field]=item.get(field)
            item[field]=None
        item["issues"].append("TEMPORARILY_UNAVAILABLE")
    item.update(data_status="TEMPORARILY_UNAVAILABLE" if unavailable else "LEGACY_UNVERIFIED",
                pnl_status="INCOMPLETE",cost_basis_status="UNKNOWN",history_status="INCOMPLETE",is_estimated=1)
    return item


def read_trades(path, exchange=EXCHANGE):
    with connect(path) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM binance_trades WHERE exchange=? ORDER BY time,symbol,trade_id",
            (exchange,))]


def _store_balances(connection, balances, valuations, exchange):
    connection.execute("DELETE FROM binance_balances WHERE exchange=?",(exchange,))
    for item in balances:
        value, status = valuations.get(item["asset"],(None,"UNAVAILABLE"))
        connection.execute("""INSERT INTO binance_balances
            (exchange,asset,free,locked,total,free_decimal,locked_decimal,total_decimal,
             value_usdc,valuation_status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (exchange,item["asset"],item["free"],item["locked"],item["total"],
             item["free_decimal"],item["locked_decimal"],item["total_decimal"],
             str(value) if value is not None else None,status,now()))


def _upsert_position(connection, position, *, exchange, owner):
    fence(connection,exchange,owner)
    existing = connection.execute(
        "SELECT * FROM positions WHERE exchange=? AND synced_from_binance=1 AND binance_asset=?",
        (exchange,position["asset"])).fetchall()
    # Legacy rows may not yet have binance_asset.
    if not existing:
        existing = connection.execute(
            "SELECT * FROM positions WHERE exchange=? AND synced_from_binance=1 AND symbol=? AND binance_asset IS NULL",
            (exchange,position["symbol"])).fetchall()
    if len(existing)>1:
        raise BinanceDataError("DUPLICATE_SYNCED_POSITION")
    previous = dict(existing[0]) if existing else None
    accounting = position["accounting"]
    if previous and previous["source"]=="MIXED":
        accounting["issues"]=sorted(set(accounting["issues"])|{"LEGACY_MIXED_ORIGIN"})
    values = {
        "exchange":exchange,"symbol":position["symbol"],"binance_asset":position["asset"],
        "base_asset":position["asset"],"resolved_symbol":position["symbol"],
        "market_source":position["market_source"],"quantity":accounting["remaining_quantity"],
        "average_buy_price":accounting["average_remaining_cost"],
        "invested_amount":accounting["invested_remaining"],
        "current_price":accounting["current_price"],"current_value":accounting["current_value"],
        "realized_pnl":accounting["realized_pnl"],"unrealized_pnl":accounting["unrealized_pnl"],
        "total_pnl":accounting["total_pnl"],"profit_loss_pct":accounting["unrealized_pnl_pct"],
        "synced_from_binance":1,"source":previous["source"] if previous else "BINANCE",
        "status":position["status"],"is_active":int(position["status"]=="ACTIVE"),
        "is_estimated":int(accounting["is_estimated"]),
        "data_status":position["data_status"],"history_status":accounting["history_status"],
        "cost_basis_status":accounting["cost_basis_status"],"price_status":accounting["price_status"],
        "pnl_status":accounting["pnl_status"],"realized_pnl_status":accounting["realized_pnl_status"],
        "accounting_json":json.dumps(accounting,allow_nan=False,sort_keys=True),
        "last_sync_at":now(),"updated_at":now(),
    }
    if previous:
        # Retain last valid facts in storage; the API exposes current uncertainty.
        for field in ("current_price","current_value","average_buy_price","invested_amount"):
            if values[field] is None:
                values[field] = previous.get(field)
        columns=list(values)
        connection.execute("UPDATE positions SET "+",".join('"'+name+'"=?' for name in columns)+" WHERE id=?",
                           (*(values[name] for name in columns),previous["id"]))
        identity=previous["id"]
    else:
        values.update(created_at=now(),strategy_type="long terme")
        columns=list(values)
        identity=connection.execute("INSERT INTO positions("+",".join(columns)+") VALUES("+
            ",".join("?" for _ in columns)+")",tuple(values[name] for name in columns)).lastrowid
    return decode_position(connection.execute("SELECT * FROM positions WHERE id=?",(identity,)).fetchone())


def _previous_asset(row):
    asset=row.get("binance_asset") or row.get("base_asset")
    if asset:
        return asset
    base,quote=split_market(row["symbol"])
    if quote or row.get("market_source")=="BINANCE_ALPHA":
        return base
    return None


def _synchronize(client, db_path, *, get_price, resolve, get_alpha, threshold, extra_symbols=(),
                exchange=None, page_size=1000, max_pages=10000):
    exchange=exchange or exchange_for_client(client)
    with monitored_lease(db_path,exchange=exchange) as owner:
        initial_balances = normalize_balances(client.get_non_zero_balances())
        with connect(db_path) as connection:
            existing = [dict(row) for row in connection.execute(
                "SELECT * FROM positions WHERE synced_from_binance=1 AND exchange=?",(exchange,))]
            stored_symbols = [row[0] for row in connection.execute(
                "SELECT DISTINCT symbol FROM binance_trades WHERE exchange=?",(exchange,))]
        discovery_complete = True
        try:
            catalog = client.get_spot_markets()
            if not isinstance(catalog,list) or not catalog:
                raise BinanceDataError("INVALID_MARKET_CATALOG")
            for item in catalog:
                if not isinstance(item,dict) or not all(
                    isinstance(item.get(k),str) and ASSET.fullmatch(item[k])
                    for k in ("symbol","base_asset","quote_asset")):
                    raise BinanceDataError("INVALID_MARKET_CATALOG")
            if any(item["symbol"]!=item["base_asset"]+item["quote_asset"] for item in catalog):
                raise BinanceDataError("INVALID_MARKET_CATALOG")
            if len({item["symbol"] for item in catalog}) != len(catalog):
                raise BinanceDataError("DUPLICATE_MARKET")
        except (BinanceReadError,ValueError,AttributeError) as error:
            if isinstance(error,BinanceReadError) and error.code in CIRCUIT_ERRORS:
                raise
            # Retain a useful partial result and explicitly mark incomplete discovery.
            catalog, discovery_complete = [],False
        unsupported=set()
        markets = {item["symbol"]:item for item in catalog if item["quote_asset"] in SUPPORTED_QUOTES}
        extra = set(stored_symbols) | {row["symbol"] for row in existing if row.get("market_source")!="BINANCE_ALPHA"} | set(extra_symbols)
        for symbol in sorted(extra):
            if not isinstance(symbol,str) or not ASSET.fullmatch(symbol.upper()):
                raise BinanceDataError("INVALID_HISTORY_SYMBOL")
            symbol=symbol.upper()
            if symbol not in markets:
                base,quote=split_market(symbol)
                if quote:
                    markets[symbol]={"symbol":symbol,"base_asset":base,"quote_asset":quote}
                else:
                    unsupported.add(symbol)
        # The catalog is authoritative for available Spot pairs. Alpha and
        # unresolved assets must not trigger private calls to invented USDC pairs.
        alpha_resolutions={}
        for balance in initial_balances:
            asset=balance["asset"]
            if asset in {"USDC","USDT","EUR"}:
                continue
            if not any(row["base_asset"]==asset for row in markets.values()):
                try:
                    resolution=resolve(asset)
                except Exception:
                    resolution={}
                if resolution.get("market_source")=="BINANCE_ALPHA":
                    alpha_resolutions[asset]=resolution
                elif not discovery_complete:
                    markets.setdefault(asset+"USDC",{"symbol":asset+"USDC","base_asset":asset,"quote_asset":"USDC"})
                else:
                    unsupported.update(row["symbol"] for row in catalog
                                       if row["base_asset"]==asset and row["quote_asset"] not in SUPPORTED_QUOTES)
        complete_markets=set()
        failures=[{"symbol":symbol,"code":"UNRESOLVED_OR_UNSUPPORTED_QUOTE"} for symbol in sorted(unsupported)]
        for market in sorted(markets.values(),key=lambda row:row["symbol"]):
            try:
                sync_market_history(market["symbol"],client,db_path,owner=owner,exchange=exchange,
                    base_asset=market["base_asset"],quote_asset=market["quote_asset"],
                    page_size=page_size,max_pages=max_pages)
                complete_markets.add(market["symbol"])
            except BinanceReadError as error:
                if error.code in CIRCUIT_ERRORS:
                    raise
                failures.append({"symbol":market["symbol"],"code":error.code})
        # A fresh complete account response is required before publishing balances.
        balances=normalize_balances(client.get_non_zero_balances())
        trades=read_trades(db_path,exchange)
        with connect(db_path) as connection:
            rates={}
            invalid_fx=0
            for row in connection.execute("SELECT * FROM historical_fx_rates WHERE exchange=?",(exchange,)):
                try:
                    value=decimal_value(row["rate"],nonnegative=True)
                    if value<=0 or not isinstance(row["time"],int) or row["time"]<0 or not row["source"]:
                        raise ValueError
                    rates[(row["asset"],row["quote_asset"],row["time"])]=value
                except (ValueError,TypeError):
                    invalid_fx+=1
        by_asset={item["asset"]:item for item in balances}
        assets={item["asset"] for item in balances if item["asset"] not in {"USDC","USDT","EUR"}}
        previous_by_asset={_previous_asset(row):row for row in existing if _previous_asset(row)}
        unresolved_previous=[row["id"] for row in existing if _previous_asset(row) is None]
        assets|=set(previous_by_asset)
        by_inventory={}
        fee_inventory_assets=set()
        for trade in trades:
            base,quote=split_market(trade["symbol"],trade.get("quote_asset"))
            if quote not in SUPPORTED_QUOTES:
                unsupported.add(trade["symbol"])
                continue
            assets.add(trade.get("base_asset") or base)
            if quote=="BTC":
                assets.add("BTC")
            fee_asset=trade.get("commission_asset")
            participants={base,quote,fee_asset}-{"USDC","USDT","EUR",None}
            for participant in participants:
                by_inventory.setdefault(participant,[]).append(trade)
            if fee_asset and fee_asset not in {"USDC","USDT","EUR"} and decimal_value(trade["commission"] or 0)>0:
                assets.add(fee_asset)
                fee_inventory_assets.add(fee_asset)
        assets-={"USDC","USDT","EUR"}
        cache={}
        def price(symbol):
            if symbol not in cache:
                try:
                    value=get_price(symbol)
                    value=None if value is None else decimal_value(value,nonnegative=True)
                    cache[symbol]=value if value is not None and value>0 else None
                except Exception as error:
                    if isinstance(error,BinanceReadError) and error.code in CIRCUIT_ERRORS:
                        raise
                    cache[symbol]=None
            return cache[symbol]
        def fx(asset):
            if asset=="USDC":
                return Decimal(1)
            direct=price(asset+"USDC")
            if direct is not None:
                return direct
            inverse=price("USDC"+asset)
            return Decimal(1)/inverse if inverse is not None else None
        snapshots=[]
        for asset in sorted(assets):
            balance=by_asset.get(asset)
            quantity=balance["total_decimal"] if balance else "0"
            positive=decimal_value(quantity)>0
            symbol=asset+"USDC"
            previous=previous_by_asset.get(asset,{})
            source=previous.get("market_source") or "BINANCE_SPOT"
            if source=="BINANCE_ALPHA":
                symbol=previous["symbol"]
            current_price=None
            converted_price=False
            if positive:
                spot=[market for market in markets.values() if market["base_asset"]==asset]
                spot.sort(key=lambda market:SUPPORTED_QUOTES.index(market["quote_asset"]))
                for market in spot:
                    quoted=price(market["symbol"])
                    conversion=fx(market["quote_asset"]) if quoted is not None else None
                    if quoted is not None and conversion is not None:
                        current_price=quoted*conversion
                        source="BINANCE_SPOT"
                        symbol=asset+"USDC"
                        converted_price=market["quote_asset"]!="USDC"
                        break
                if current_price is None and asset=="BTC":
                    current_price=fx(asset)
                if current_price is None and not any(
                    item["base_asset"]==asset for item in catalog):
                    try:
                        resolution=alpha_resolutions.get(asset) or resolve(asset)
                        if resolution.get("market_source")=="BINANCE_ALPHA":
                            source="BINANCE_ALPHA"
                            symbol=resolution["symbol"]
                            data=get_alpha(asset)
                            quote=data.get("price_quote_asset") or data.get("quote_asset")
                            if not quote:
                                _,quote=split_market(data.get("alpha_trading_symbol",""))
                            # Token fallback prices without a quote are not a
                            # verified financial valuation. Prefer raw ticker decimals.
                            ticker=data.get("ticker") or {}
                            raw=ticker.get("lastPrice",ticker.get("price"))
                            if raw is None and (data.get("price_quote_asset") or data.get("quote_asset")):
                                raw=data.get("current_price")
                            value=None if raw is None else decimal_value(raw,nonnegative=True)
                            conversion=fx(quote) if quote else None
                            if value is not None and value>0 and conversion is not None:
                                current_price=value*conversion
                                symbol=data.get("symbol") or resolution["symbol"]
                                converted_price=quote!="USDC"
                    except Exception as error:
                        if isinstance(error,BinanceReadError) and error.code in CIRCUIT_ERRORS:
                            raise
                        current_price=None
            relevant={market["symbol"] for market in markets.values()
                      if asset in {market["base_asset"],market["quote_asset"]}}
            # Fees paid in an asset can originate on any supported market.
            if asset in fee_inventory_assets:
                relevant=set(markets)
            history_complete=(discovery_complete and not unsupported and relevant<=complete_markets and source!="BINANCE_ALPHA")
            accounting=calculate_fifo_pnl(by_inventory.get(asset,[]),symbol,current_price,quantity,
                history_complete=history_complete,historical_rates=rates,valuation_estimated=converted_price,
                funding_history_complete=False)
            value=accounting["current_value"]
            if positive:
                status="DUST" if value is not None and decimal_value(accounting["decimal_values"]["current_value"])<decimal_value(threshold) else "ACTIVE"
            else:
                status=("CLOSED" if history_complete and
                        accounting["decimal_values"]["calculated_remaining_quantity"] is not None and
                        decimal_value(accounting["decimal_values"]["calculated_remaining_quantity"])==0 and
                        "SALE_WITH_UNKNOWN_COST" not in accounting["issues"] else "INACTIVE")
            data_status=("TEMPORARILY_UNAVAILABLE" if positive and current_price is None else
                         "HISTORY_INCOMPLETE" if not history_complete else
                         "COST_UNKNOWN" if accounting["invested_remaining"] is None else
                         "CLOSED" if status=="CLOSED" else "VALID")
            snapshots.append({"asset":asset,"symbol":symbol,"market_source":source,
                              "status":status,"data_status":data_status,"accounting":accounting})
        failures.extend({"symbol":symbol,"code":"UNRESOLVED_OR_UNSUPPORTED_QUOTE"} for symbol in sorted(unsupported) if not any(item["symbol"]==symbol for item in failures))
        valuations={}
        for balance in balances:
            asset=balance["asset"]
            if asset in {"USDC","USDT","EUR"}:
                rate=fx(asset)
                valuations[asset]=(None if rate is None else decimal_value(balance["total_decimal"])*rate,
                                   "EXACT" if asset=="USDC" else "ESTIMATED" if rate else "UNAVAILABLE")
        successful=discovery_complete and not unsupported and len(complete_markets)==len(markets)
        with connect(db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            fence(connection,exchange,owner)
            _store_balances(connection,balances,valuations,exchange)
            for identity in unresolved_previous:
                connection.execute("""UPDATE positions SET data_status='TEMPORARILY_UNAVAILABLE',
                    price_status='UNAVAILABLE',pnl_status='INCOMPLETE' WHERE id=?""",(identity,))
            # Legacy synthetic cash positions are archived without erasing their
            # quantities/history; their current value belongs to balances once.
            connection.execute("""UPDATE positions SET status='INACTIVE',is_active=0,
                data_status='CASH_BALANCE_ONLY' WHERE exchange=? AND synced_from_binance=1
                AND (binance_asset IN ('USDC','USDT','EUR') OR base_asset IN ('USDC','USDT','EUR'))""",(exchange,))
            positions=[_upsert_position(connection,item,exchange=exchange,owner=owner) for item in snapshots]
            connection.execute("""INSERT INTO exchange_sync_state
                (exchange,last_sync,sync_status,last_attempt,markets_complete,markets_total,discovery_status)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(exchange) DO UPDATE SET
                last_sync=CASE WHEN excluded.sync_status='COMPLETE' THEN excluded.last_sync ELSE exchange_sync_state.last_sync END,
                sync_status=excluded.sync_status,last_attempt=excluded.last_attempt,
                markets_complete=excluded.markets_complete,markets_total=excluded.markets_total,
                discovery_status=excluded.discovery_status""",
                (exchange,now() if successful else None,"COMPLETE" if successful else "PARTIAL",now(),
                 len(complete_markets),len(markets)+len(unsupported),"COMPLETE" if discovery_complete and not unsupported else "INCOMPLETE"))
        return {"positions":[p for p in positions if p["status"]=="ACTIVE"],
                "dust":[{**p,"current_value_usdc":p["current_value"],
                         "reason":f"Valeur inf\u00e9rieure au seuil actif de {threshold:g} USDC"}
                        for p in positions if p["status"]=="DUST"],
                "closed":[p for p in positions if p["status"]=="CLOSED"],
                "synced":successful,"sync_status":"COMPLETE" if successful else "PARTIAL",
                "markets_complete":len(complete_markets),"markets_total":len(markets)+len(unsupported),
                "discovery_status":"COMPLETE" if discovery_complete and not unsupported else "INCOMPLETE",
                "market_errors":failures,"public_price_calls":len(cache),"invalid_historical_fx_records":invalid_fx,
                "coverage_scope":"AVAILABLE_AND_STORED_SPOT_MARKETS_USDC_USDT_EUR_BTC",
                "funding_history_status":"NOT_DOCUMENTED"}


@contextmanager
def monitored_lease(db_path, *, exchange):
    with sync_lease(db_path,exchange=exchange) as owner:
        try:
            yield owner
        except LeaseLost:
            raise
        except BaseException:
            with connect(db_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                fence(connection,exchange,owner)
                connection.execute("""UPDATE positions SET data_status='TEMPORARILY_UNAVAILABLE',
                    price_status='UNAVAILABLE',pnl_status='INCOMPLETE',realized_pnl_status='INCOMPLETE'
                    WHERE exchange=? AND synced_from_binance=1""",(exchange,))
                connection.execute("""INSERT INTO exchange_sync_state(exchange,sync_status,last_attempt)
                    VALUES(?,'ERROR',?) ON CONFLICT(exchange) DO UPDATE SET sync_status='ERROR',last_attempt=excluded.last_attempt""",(exchange,now()))
            raise



def synchronize(*args,**kwargs):
    with localcontext() as context:
        context.prec=256
        return _synchronize(*args,**kwargs)
