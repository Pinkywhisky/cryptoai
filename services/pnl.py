from typing import Any


QUOTE_ASSET = "USDC"
MIN_COST_BASIS_FOR_PCT = 1.0
FLOAT_TOLERANCE = 1e-10


def base_asset_from_symbol(symbol: str, quote_asset: str = QUOTE_ASSET) -> str:
    return symbol.upper().removesuffix(quote_asset)


def _trade_side(trade: dict[str, Any]) -> str:
    if "side" in trade:
        return str(trade["side"]).upper()
    return "BUY" if bool(trade.get("isBuyer") or trade.get("is_buyer")) else "SELL"


def _trade_quote_qty(trade: dict[str, Any]) -> float:
    return float(trade.get("quoteQty", trade.get("quote_qty", 0)) or 0)


def _trade_commission_in_quote(
    trade: dict[str, Any],
    quote_asset: str = QUOTE_ASSET,
) -> float:
    commission_asset = trade.get("commissionAsset", trade.get("commission_asset"))
    if commission_asset == quote_asset:
        return float(trade.get("commission", 0) or 0)
    return 0.0


def _trade_qty_after_base_commission(
    trade: dict[str, Any],
    symbol: str,
    quote_asset: str = QUOTE_ASSET,
) -> tuple[float, bool]:
    qty = float(trade.get("qty", 0) or 0)
    commission_asset = trade.get("commissionAsset", trade.get("commission_asset"))
    if commission_asset == base_asset_from_symbol(symbol, quote_asset=quote_asset):
        return max(0.0, qty - float(trade.get("commission", 0) or 0)), True
    if commission_asset not in (None, "", quote_asset):
        return qty, True
    return qty, False


def calculate_fifo_pnl(
    trades: list[dict[str, Any]],
    symbol: str,
    current_price: float | None,
    current_balance: float,
    quote_asset: str = QUOTE_ASSET,
) -> dict[str, Any]:
    lots: list[dict[str, float]] = []
    realized_pnl = 0.0
    is_estimated = False

    ordered_trades = sorted(trades, key=lambda trade: int(trade.get("time", 0) or 0))

    for trade in ordered_trades:
        side = _trade_side(trade)
        qty = float(trade.get("qty", 0) or 0)
        quote_qty = _trade_quote_qty(trade)
        quote_commission = _trade_commission_in_quote(trade, quote_asset=quote_asset)

        if side == "BUY":
            acquired_qty, estimated_commission = _trade_qty_after_base_commission(
                trade,
                symbol,
                quote_asset=quote_asset,
            )
            is_estimated = is_estimated or estimated_commission
            if acquired_qty <= FLOAT_TOLERANCE:
                is_estimated = True
                continue
            lots.append({"qty": acquired_qty, "cost": quote_qty + quote_commission})
            continue

        remaining_to_sell = qty
        proceeds = quote_qty - quote_commission
        cost_sold = 0.0

        while remaining_to_sell > FLOAT_TOLERANCE and lots:
            lot = lots[0]
            consumed_qty = min(remaining_to_sell, lot["qty"])
            unit_cost = lot["cost"] / lot["qty"] if lot["qty"] else 0.0
            consumed_cost = consumed_qty * unit_cost
            cost_sold += consumed_cost

            lot["qty"] -= consumed_qty
            lot["cost"] -= consumed_cost
            remaining_to_sell -= consumed_qty

            if lot["qty"] <= FLOAT_TOLERANCE:
                lots.pop(0)

        if remaining_to_sell > FLOAT_TOLERANCE:
            is_estimated = True
            covered_fraction = (qty - remaining_to_sell) / qty if qty else 0.0
            proceeds *= covered_fraction

        realized_pnl += proceeds - cost_sold

    calculated_remaining_qty = sum(lot["qty"] for lot in lots)
    calculated_cost_basis = sum(lot["cost"] for lot in lots)
    average_remaining_cost = (
        calculated_cost_basis / calculated_remaining_qty
        if calculated_remaining_qty > FLOAT_TOLERANCE
        else 0.0
    )

    current_balance = max(0.0, float(current_balance or 0))
    if abs(current_balance - calculated_remaining_qty) > 1e-8:
        is_estimated = True

    invested_remaining = current_balance * average_remaining_cost
    current_value = current_balance * current_price if current_price is not None else 0.0
    unrealized_pnl = current_value - invested_remaining if current_balance > 0 else 0.0
    unrealized_pnl_pct = (
        round(unrealized_pnl / invested_remaining * 100, 4)
        if invested_remaining >= MIN_COST_BASIS_FOR_PCT
        else None
    )

    return {
        "remaining_quantity": round(current_balance, 8),
        "calculated_remaining_quantity": round(calculated_remaining_qty, 8),
        "average_remaining_cost": round(average_remaining_cost, 8),
        "invested_remaining": round(invested_remaining, 8),
        "current_price": current_price,
        "current_value": round(current_value, 8),
        "realized_pnl": round(realized_pnl, 8),
        "unrealized_pnl": round(unrealized_pnl, 8),
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "total_pnl": round(realized_pnl + unrealized_pnl, 8),
        "is_estimated": is_estimated,
    }
