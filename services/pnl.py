"""Conservative FIFO in Decimal. Missing facts never become zero-valued facts.

Amounts are reported in quote_asset (USDC by default). Historical FX is supplied
explicitly as {(asset, quote_asset, millisecond_timestamp): Decimal rate}.
No stablecoin parity, current FX or invented historical price is substituted.
"""
from collections import deque
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

QUOTE_ASSET = "USDC"
SUPPORTED_QUOTES = ("USDC", "USDT", "EUR", "BTC")
MIN_COST_BASIS_FOR_PCT = Decimal("1")
ZERO = Decimal(0)


def decimal_value(value, *, nonnegative=False):
    if isinstance(value, bool) or value is None:
        raise ValueError("Invalid financial number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid financial number") from None
    if not result.is_finite() or (nonnegative and result < 0):
        raise ValueError("Invalid financial number")
    # Bound pathological exponents/coefficients before accounting operations.
    if result and (abs(result.adjusted()) > 300 or len(result.as_tuple().digits) > 100):
        raise ValueError("Financial number outside supported precision")
    return result


def split_market(symbol, quote_asset=None):
    symbol = str(symbol).strip().upper()
    if quote_asset and symbol.endswith(quote_asset) and len(symbol) > len(quote_asset):
        return symbol[:-len(quote_asset)], quote_asset
    for quote in SUPPORTED_QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[:-len(quote)], quote
    return symbol, None


def base_asset_from_symbol(symbol, quote_asset=QUOTE_ASSET):
    return split_market(symbol)[0]


def _side(trade):
    side = trade.get("side")
    if side is not None:
        side = str(side).upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("Invalid trade side")
        return side
    flag = trade.get("isBuyer", trade.get("is_buyer"))
    if flag not in (True, False, 0, 1) or flag is None:
        raise ValueError("Missing trade side")
    return "BUY" if flag else "SELL"


def _rate(asset, target, timestamp, rates):
    if asset == target:
        return Decimal(1)
    value = rates.get((asset, target, timestamp))
    if value is None:
        return None
    value = decimal_value(value, nonnegative=True)
    if value <= 0:
        raise ValueError("Invalid historical conversion")
    return value


def _multiply(value, rate):
    return None if rate is None else value * rate


def _events(trades, asset, symbol, target, rates):
    events = []
    missing = set()
    precisions = set()
    identities = {}
    for ordinal, trade in enumerate(trades):
        market = str(trade.get("symbol") or symbol).upper()
        base, quote = split_market(market, trade.get("quote_asset"))
        base = trade.get("base_asset") or base
        if not quote:
            missing.add("UNKNOWN_QUOTE_CURRENCY")
            continue
        timestamp = int(trade.get("time") or 0)
        trade_id = trade.get("trade_id", trade.get("id"))
        if trade_id is not None:
            identity = (trade.get("exchange", "BINANCE_SPOT"), market, int(trade_id))
            economic = tuple(str(trade.get(k)) for k in (
                "side", "isBuyer", "is_buyer", "qty", "price", "quoteQty", "quote_qty",
                "commission", "commissionAsset", "commission_asset", "time"))
            if identity in identities:
                if identities[identity] != economic:
                    raise ValueError("Conflicting duplicate trade")
                continue
            identities[identity] = economic
        quantity = decimal_value(trade.get("qty"), nonnegative=True)
        if quantity <= 0:
            raise ValueError("Trade quantity must be positive")
        quote_raw = trade.get("quoteQty", trade.get("quote_qty"))
        gross = (decimal_value(quote_raw, nonnegative=True) if quote_raw is not None
                 else quantity * decimal_value(trade.get("price"), nonnegative=True))
        fee = decimal_value(trade.get("commission", 0), nonnegative=True)
        fee_asset = str(trade.get("commissionAsset", trade.get("commission_asset")) or "").upper()
        side = _side(trade)
        rate = _rate(quote, target, timestamp, rates)
        fee_rate = _rate(fee_asset, target, timestamp, rates) if fee and fee_asset else None
        if asset not in {base, quote, fee_asset}:
            continue
        if trade.get("precision_status") == "LEGACY_FLOAT":
            precisions.add("LEGACY_FLOAT")
        if (quote != target and rate is not None) or (
            fee and fee_asset not in {base,target} and fee_rate is not None):
            precisions.add("HISTORICAL_FX_CONVERSION")
        key = (timestamp, market, int(trade_id) if trade_id is not None else ordinal)
        if asset == base:
            fee_in_base = fee if fee_asset == base else ZERO
            if side == "BUY" and fee_in_base >= quantity:
                raise ValueError("Base commission exceeds acquired quantity")
            amount = quantity - fee_in_base if side == "BUY" else quantity + fee_in_base
            value = _multiply(gross, rate)
            if fee and fee_asset != base:
                converted = _multiply(fee, fee_rate)
                if converted is None:
                    missing.add("MISSING_COMMISSION_CONVERSION")
                    value = None
                elif value is not None:
                    value += converted if side == "BUY" else -converted
            if rate is None:
                missing.add("MISSING_TRADE_CONVERSION")
            events.append((key, side, amount, value))
        # A crypto quote is also inventory: spending BTC disposes of its lots.
        elif asset == quote and quote not in {"USDC", "USDT", "EUR"}:
            quote_fee = fee if fee_asset == quote else ZERO
            amount = gross + quote_fee if side == "BUY" else gross - quote_fee
            if amount < 0:
                raise ValueError("Quote commission exceeds proceeds")
            events.append((key, "SELL" if side == "BUY" else "BUY",
                           amount, _multiply(amount, rate)))
            if rate is None:
                missing.add("MISSING_TRADE_CONVERSION")
        elif asset == fee_asset and fee:
            # Third-currency payment is a disposal at documented historical FX.
            events.append((key, "SELL", fee, _multiply(fee, fee_rate)))
            if fee_rate is None:
                missing.add("MISSING_COMMISSION_CONVERSION")
    return sorted(events, key=lambda event: event[0]), missing, precisions


def _number(value):
    if value is None:
        return None
    converted = float(value)
    if converted in (float("inf"), float("-inf")) or (converted == 0 and value != 0):
        return None
    return converted


def calculate_fifo_pnl(trades: list[dict[str, Any]], symbol: str,
                       current_price=None, current_balance=None,
                       quote_asset: str = QUOTE_ASSET, *, history_complete=True,
                       historical_rates=None, valuation_estimated=False,
                       movements=None, funding_history_complete=True) -> dict[str, Any]:
    with localcontext() as context:
        context.prec = 256
        asset = base_asset_from_symbol(symbol)
        events, issues, precisions = _events(trades, asset, symbol, quote_asset,
                                             historical_rates or {})
        if "UNKNOWN_QUOTE_CURRENCY" in issues:
            history_complete=False
        movements_seen=set()
        for ordinal,movement in enumerate(movements or []):
            if movement.get("asset")!=asset:
                continue
            kind=movement.get("type")
            if kind not in {"TRANSFER_IN","TRANSFER_OUT"}:
                raise ValueError("Invalid inventory movement")
            quantity=decimal_value(movement.get("quantity"),nonnegative=True)
            if quantity<=0:
                raise ValueError("Invalid inventory movement quantity")
            timestamp=movement.get("time")
            if isinstance(timestamp,bool) or not isinstance(timestamp,int) or timestamp<0:
                raise ValueError("Invalid inventory movement time")
            identity=(movement.get("source","EXPLICIT"),str(movement.get("id",ordinal)))
            if identity in movements_seen:
                raise ValueError("Duplicate inventory movement")
            movements_seen.add(identity)
            cost=movement.get("cost_basis")
            cost=None if cost is None else decimal_value(cost,nonnegative=True)
            if cost is not None:
                currency=movement.get("cost_basis_quote")
                if not isinstance(currency,str) or not currency:
                    raise ValueError("Movement cost requires an explicit currency")
                rate=_rate(currency,quote_asset,timestamp,historical_rates or {})
                if rate is None:
                    issues.add("MISSING_MOVEMENT_CONVERSION")
                    cost=None
                else:
                    cost*=rate
                    if currency!=quote_asset:
                        precisions.add("HISTORICAL_FX_CONVERSION")
            events.append(((timestamp,"MOVEMENT:"+identity[0],ordinal),kind,quantity,cost))
        events.sort(key=lambda event:event[0])
        by_time={}
        for key,side,quantity,value in events:
            by_time.setdefault(key[0],set()).add(key[1])
        if any(len(markets)>1 for markets in by_time.values()):
            issues.add("AMBIGUOUS_EVENT_ORDER")
            history_complete=False
        lots = deque()
        realized = ZERO
        realized_known = ZERO
        realized_complete = bool(history_complete)
        unmatched_sells = ZERO
        transferred_cost = ZERO
        transfer_cost_complete=True
        for _, side, quantity, value in events:
            if side in {"BUY","TRANSFER_IN"}:
                if quantity > 0:
                    lots.append([quantity, value])
                continue
            left = quantity
            sold_cost = ZERO
            complete = value is not None
            consumed_cost_unknown=False
            while left > 0 and lots:
                lot_quantity, lot_cost = lots[0]
                consumed = min(left, lot_quantity)
                cost = None if lot_cost is None else lot_cost * consumed / lot_quantity
                if cost is None:
                    complete = False
                    consumed_cost_unknown=True
                else:
                    sold_cost += cost
                left -= consumed
                if consumed == lot_quantity:
                    lots.popleft()
                else:
                    lots[0] = [lot_quantity-consumed,
                               None if lot_cost is None else lot_cost-cost]
            if side=="TRANSFER_OUT":
                if left or consumed_cost_unknown:
                    issues.add("TRANSFER_WITH_UNKNOWN_COST")
                    transfer_cost_complete=False
                    if left:
                        history_complete=False
                transferred_cost+=sold_cost
                continue
            if left:
                unmatched_sells += left
                issues.add("SALE_WITH_UNKNOWN_COST")
                complete = False
            if complete:
                realized_known += value - sold_cost
                realized += value - sold_cost
            else:
                realized_complete = False
        calculated_quantity = sum((lot[0] for lot in lots), ZERO)
        known_cost = sum((lot[1] for lot in lots if lot[1] is not None), ZERO)
        unknown_lots = any(lot[1] is None for lot in lots)
        balance = calculated_quantity if current_balance is None else decimal_value(current_balance, nonnegative=True)
        # Native Binance decimal balances are exact strings. Even a tiny
        # unexplained difference is evidence of an unknown inventory movement.
        mismatch = balance != calculated_quantity
        if mismatch:
            issues.add("BALANCE_MISMATCH")
        if not history_complete:
            issues.add("HISTORY_INCOMPLETE")
        price = None if current_price is None else decimal_value(current_price, nonnegative=True)
        if price is not None and price <= 0:
            price = None
        cost_complete = bool(history_complete) and not unknown_lots and not mismatch and not unmatched_sells
        invested = known_cost if cost_complete else None
        average = (invested / balance if balance else ZERO) if invested is not None else None
        current_value = ZERO if balance == 0 else (_multiply(balance, price))
        if balance and price is None:
            issues.add("MARKET_PRICE_UNAVAILABLE")
        unrealized = current_value-invested if current_value is not None and invested is not None else None
        if mismatch:
            realized_complete = False
        realized_amount = realized if realized_complete else None
        total = (realized_amount+unrealized
                 if realized_amount is not None and unrealized is not None else None)
        pct = (unrealized/invested*100
               if unrealized is not None and invested is not None and invested >= MIN_COST_BASIS_FOR_PCT
               else None)
        if not funding_history_complete:
            precisions.add("FUNDING_HISTORY_NOT_DOCUMENTED")
        values = {
            "remaining_quantity": balance, "calculated_remaining_quantity": calculated_quantity,
            "average_remaining_cost": average, "invested_remaining": invested,
            "current_price": price, "current_value": current_value,
            "realized_pnl": realized_amount, "known_realized_pnl": realized_known if history_complete and not mismatch else None,
            "unrealized_pnl": unrealized, "unrealized_pnl_pct": pct, "total_pnl": total,
            "known_remaining_cost": known_cost, "unmatched_sale_quantity": unmatched_sells,
            "transferred_cost_basis":transferred_cost if transfer_cost_complete else None,
        }
        cost_status = ("EXACT" if cost_complete else
                       "UNKNOWN" if not events and balance else "INCOMPLETE")
        price_status = "EXACT" if price is not None or balance == 0 else "UNAVAILABLE"
        realized_status = "EXACT" if realized_amount is not None else "INCOMPLETE"
        pnl_status = "EXACT" if total is not None else (
            "UNAVAILABLE" if price_status == "UNAVAILABLE" and cost_complete else "INCOMPLETE")
        if valuation_estimated and price_status=="EXACT":
            price_status="ESTIMATED"
            if pnl_status=="EXACT":
                pnl_status="ESTIMATED"
        if precisions:
            if cost_status == "EXACT":
                cost_status = "ESTIMATED"
            if realized_status == "EXACT":
                realized_status = "ESTIMATED"
            if pnl_status == "EXACT":
                pnl_status = "ESTIMATED"
        return {
            **{key: _number(value) for key, value in values.items()},
            "decimal_values": {key: str(value) if value is not None else None
                               for key,value in values.items()},
            "asset": asset, "quote_asset": quote_asset,
            "history_status": "COMPLETE" if history_complete else "INCOMPLETE",
            "funding_history_status":"COMPLETE" if funding_history_complete else "NOT_DOCUMENTED",
            "cost_basis_status": cost_status, "price_status": price_status,
            "realized_pnl_status": realized_status, "pnl_status": pnl_status,
            "issues": sorted(issues | precisions),
            "is_estimated": any(value != "EXACT" for value in (
                cost_status, price_status, realized_status, pnl_status)),
        }


def value_declared_position(quantity, invested_amount, current_price):
    """Value an owner's declared USDC basis; this is not a trade-history reconstruction."""
    with localcontext() as context:
        context.prec=256
        quantity=decimal_value(quantity,nonnegative=True)
        basis=None if invested_amount is None else decimal_value(invested_amount,nonnegative=True)
        price=None if current_price is None else decimal_value(current_price,nonnegative=True)
        if price is not None and price<=0:
            price=None
        value=ZERO if quantity==0 else _multiply(quantity,price)
        gain=value-basis if value is not None and basis is not None else None
        pct=gain/basis*100 if gain is not None and basis is not None and basis>0 else None
        values={"remaining_quantity":quantity,"invested_remaining":basis,
                "current_price":price,"current_value":value,"unrealized_pnl":gain,
                "unrealized_pnl_pct":pct}
        return {
            **{key:_number(value) for key,value in values.items()},
            "decimal_values":{key:str(value) if value is not None else None for key,value in values.items()},
            "gain_loss_amount":_number(gain),
            "gain_loss_pct":round(_number(pct),4) if pct is not None and _number(pct) is not None else None,
            "cost_basis_status":"ESTIMATED" if basis is not None else "UNKNOWN",
            "price_status":"EXACT" if price is not None or quantity==0 else "UNAVAILABLE",
            "pnl_status":"ESTIMATED" if gain is not None else "INCOMPLETE",
            "funding_history_status":"OWNER_DECLARED","is_estimated":True,
            "data_status":"COST_UNKNOWN" if basis is None else "TEMPORARILY_UNAVAILABLE" if value is None else "VALID",
        }
