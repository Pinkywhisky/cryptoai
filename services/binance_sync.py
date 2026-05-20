import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from services.binance_alpha import MARKET_SOURCE_ALPHA, MARKET_SOURCE_SPOT, fetch_alpha_market_data
from services.binance_private_client import BinancePrivateClient, get_private_client_status
from services.config import get_min_active_position_value_usdc
from services.history import DB_PATH, init_db
from services.pnl import calculate_fifo_pnl
from services.scanner import analyze_symbol_multi
from services.symbols import resolve_market_symbol


QUOTE_ASSET = "USDC"
MIN_POSITION_AMOUNT = 0.00000001
MIN_ACTIVE_POSITION_VALUE_USDC = get_min_active_position_value_usdc()
MIN_POSITION_VALUE_USDC = MIN_ACTIVE_POSITION_VALUE_USDC
PUBLIC_BASE_URL = "https://api.binance.com"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    init_db(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _get_existing_position(symbol: str, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM positions WHERE symbol = ? LIMIT 1",
            (symbol,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def _symbol_from_asset(asset: str) -> str:
    return f"{asset.upper()}{QUOTE_ASSET}"


def asset_to_symbol(asset: str, quote_asset: str = QUOTE_ASSET) -> str:
    return f"{asset.upper()}{quote_asset.upper()}"


def symbol_exists(symbol: str) -> bool:
    try:
        response = requests.get(
            f"{PUBLIC_BASE_URL}/api/v3/ticker/price",
            params={"symbol": symbol.upper()},
            timeout=10,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


def get_current_price(symbol: str) -> float | None:
    try:
        response = requests.get(
            f"{PUBLIC_BASE_URL}/api/v3/ticker/price",
            params={"symbol": symbol.upper()},
            timeout=10,
        )
        response.raise_for_status()
        return float(response.json()["price"])
    except (requests.RequestException, KeyError, TypeError, ValueError):
        return None


def sync_balances(
    client: BinancePrivateClient | None = None,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    client = client or BinancePrivateClient()
    if not client.configured:
        return []

    balances = client.get_non_zero_balances()
    updated_at = _now()
    with _connect(db_path) as connection:
        connection.execute("DELETE FROM binance_balances")
        for balance in balances:
            connection.execute(
                """
                INSERT INTO binance_balances (asset, free, locked, total, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(asset) DO UPDATE SET
                    free = excluded.free,
                    locked = excluded.locked,
                    total = excluded.total,
                    updated_at = excluded.updated_at
                """,
                (
                    balance["asset"],
                    balance["free"],
                    balance["locked"],
                    balance["total"],
                    updated_at,
                ),
            )
        connection.commit()
    return balances


def is_active_position_value(current_value_usdc: float | None) -> bool:
    return (
        current_value_usdc is not None
        and current_value_usdc >= MIN_ACTIVE_POSITION_VALUE_USDC
    )


def sync_trades(
    symbol: str,
    client: BinancePrivateClient | None = None,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    client = client or BinancePrivateClient()
    if not client.configured:
        return []

    trades = client.get_my_trades(symbol)
    with _connect(db_path) as connection:
        for trade in trades:
            is_buyer = bool(trade.get("isBuyer"))
            connection.execute(
                """
                INSERT OR IGNORE INTO binance_trades (
                    symbol, order_id, trade_id, side, price, qty, quote_qty,
                    commission, commission_asset, time, is_buyer, is_maker
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol.upper(),
                    trade.get("orderId"),
                    trade.get("id"),
                    "BUY" if is_buyer else "SELL",
                    float(trade.get("price", 0)),
                    float(trade.get("qty", 0)),
                    float(trade.get("quoteQty", 0)),
                    float(trade.get("commission", 0)),
                    trade.get("commissionAsset"),
                    trade.get("time"),
                    int(is_buyer),
                    int(bool(trade.get("isMaker"))),
                ),
            )
        connection.commit()
    return trades


def get_stored_trades(symbol: str, db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    with _connect(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM binance_trades WHERE symbol = ? ORDER BY time ASC",
            (symbol.upper(),),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def rebuild_fifo_position(
    trades: list[dict[str, Any]],
    symbol: str,
    current_price: float | None = None,
    current_balance: float | None = None,
) -> dict[str, Any]:
    calculated_qty = sum(float(trade["qty"]) if trade["side"] == "BUY" else -float(trade["qty"]) for trade in trades)
    balance = calculated_qty if current_balance is None else current_balance
    result = calculate_fifo_pnl(
        trades=trades,
        symbol=symbol,
        current_price=current_price,
        current_balance=balance,
    )
    return {
        **result,
        "quantity": result["remaining_quantity"],
        "remaining_qty": result["remaining_quantity"],
        "average_buy_price": result["average_remaining_cost"],
        "invested_amount": result["invested_remaining"],
    }


def estimate_average_buy_price(symbol: str, db_path: Path | str = DB_PATH) -> dict[str, Any]:
    trades = get_stored_trades(symbol, db_path=db_path)
    return rebuild_fifo_position(trades, symbol)


def calculate_realized_pnl(symbol: str, db_path: Path | str = DB_PATH) -> float:
    return estimate_average_buy_price(symbol, db_path=db_path)["realized_pnl"]


def calculate_unrealized_pnl(
    symbol: str,
    current_price: float | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    trades = get_stored_trades(symbol, db_path=db_path)
    if current_price is None:
        snapshot = analyze_symbol_multi(symbol=symbol, limit=120)
        current_price = snapshot.get("current_price")
    return rebuild_fifo_position(trades, symbol, current_price=current_price)


def sync_trades_for_symbols(
    symbols: list[str],
    client: BinancePrivateClient | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, list[dict[str, Any]]]:
    return {
        symbol.upper(): sync_trades(symbol, client=client, db_path=db_path)
        for symbol in symbols
    }


def _tradable_balances(balances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        balance
        for balance in balances
        if balance["asset"] != QUOTE_ASSET
        and float(balance["total"]) > MIN_POSITION_AMOUNT
    ]


def rebuild_active_positions_from_balances(
    balances: list[dict[str, Any]],
    client: BinancePrivateClient | None = None,
    db_path: Path | str = DB_PATH,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active_positions: list[dict[str, Any]] = []
    dust: list[dict[str, Any]] = []
    client = client or BinancePrivateClient()

    for balance in _tradable_balances(balances):
        asset = balance["asset"]
        symbol = asset_to_symbol(asset)
        quantity = float(balance["total"])

        market_source = MARKET_SOURCE_SPOT
        resolved_symbol = symbol
        base_asset = asset
        alpha_data: dict[str, Any] = {}
        if symbol_exists(symbol):
            current_price = get_current_price(symbol)
        else:
            resolution = resolve_market_symbol(asset)
            if resolution.get("market_source") != MARKET_SOURCE_ALPHA:
                continue
            alpha_data = fetch_alpha_market_data(asset)
            current_price = alpha_data.get("current_price")
            resolved_symbol = alpha_data.get("symbol") or resolution["symbol"]
            symbol = resolved_symbol
            market_source = MARKET_SOURCE_ALPHA

        if current_price is None:
            continue

        current_value_usdc = quantity * current_price
        if not is_active_position_value(current_value_usdc):
            dust.append(
                {
                    "asset": asset,
                    "symbol": symbol,
                    "quantity": round(quantity, 8),
                    "current_price": current_price,
                    "current_value_usdc": round(current_value_usdc, 8),
                    "reason": f"Valeur inférieure au seuil actif de {MIN_ACTIVE_POSITION_VALUE_USDC:g} USDC",
                }
            )
            continue

        if market_source == MARKET_SOURCE_ALPHA:
            existing = _get_existing_position(symbol, db_path=db_path)
            average_buy_price = float(existing.get("average_buy_price") or current_price) if existing else current_price
            invested_amount = float(existing.get("invested_amount") or (average_buy_price * quantity)) if existing else average_buy_price * quantity
            unrealized_pnl = round(current_value_usdc - invested_amount, 8)
            pnl = {
                "remaining_quantity": quantity,
                "average_remaining_cost": average_buy_price,
                "invested_remaining": invested_amount,
                "current_price": current_price,
                "current_value": current_value_usdc,
                "realized_pnl": float(existing.get("realized_pnl") or 0) if existing else 0,
                "unrealized_pnl": unrealized_pnl,
                "total_pnl": unrealized_pnl + (float(existing.get("realized_pnl") or 0) if existing else 0),
                "unrealized_pnl_pct": round(unrealized_pnl / invested_amount * 100, 8) if invested_amount else None,
                "is_estimated": True,
            }
        else:
            sync_trades(symbol, client=client, db_path=db_path)
            trades = get_stored_trades(symbol, db_path=db_path)
            pnl = calculate_fifo_pnl(
                trades=trades,
                symbol=symbol,
                current_price=current_price,
                current_balance=quantity,
            )
        active_positions.append(
            {
                "asset": asset,
                "symbol": symbol,
                "resolved_symbol": resolved_symbol,
                "base_asset": base_asset,
                "market_source": market_source,
                "quantity": pnl["remaining_quantity"],
                "average_buy_price": pnl["average_remaining_cost"],
                "invested_amount": pnl["invested_remaining"],
                "current_price": pnl["current_price"],
                "current_value": pnl["current_value"],
                "realized_pnl": pnl["realized_pnl"],
                "unrealized_pnl": pnl["unrealized_pnl"],
                "total_pnl": pnl["total_pnl"],
                "profit_loss_pct": pnl["unrealized_pnl_pct"],
                "is_estimated": int(pnl["is_estimated"]),
                "price_change_pct": alpha_data.get("price_change_pct"),
                "quote_volume": alpha_data.get("quote_volume"),
                "market_cap": alpha_data.get("market_cap"),
            }
        )

    return active_positions, dust


def _upsert_synced_position(
    position: dict[str, Any],
    status: str,
    is_active: bool,
    now: str,
    db_path: Path | str = DB_PATH,
) -> None:
    with _connect(db_path) as connection:
        existing = connection.execute(
            "SELECT id, source FROM positions WHERE symbol = ?",
            (position["symbol"],),
        ).fetchone()
        source = "BINANCE" if not existing else (
            "MIXED" if existing["source"] == "MANUAL" else existing["source"]
        )
        values = {
            "symbol": position["symbol"],
            "quantity": position.get("quantity", 0),
            "average_buy_price": position.get("average_buy_price", 0),
            "invested_amount": position.get("invested_amount", 0),
            "current_price": position.get("current_price"),
            "current_value": position.get("current_value", position.get("current_value_usdc", 0)),
            "strategy_type": "long terme",
            "source": source,
            "binance_asset": position.get("asset"),
            "synced_from_binance": 1,
            "is_active": 1 if is_active else 0,
            "status": status,
            "last_sync_at": now,
            "realized_pnl": position.get("realized_pnl", 0),
            "unrealized_pnl": position.get("unrealized_pnl", 0),
            "total_pnl": position.get("total_pnl", 0),
            "profit_loss_pct": position.get("profit_loss_pct"),
            "is_estimated": position.get("is_estimated", 0),
            "market_source": position.get("market_source", MARKET_SOURCE_SPOT),
            "resolved_symbol": position.get("resolved_symbol", position["symbol"]),
            "base_asset": position.get("base_asset", position.get("asset")),
            "updated_at": now,
        }
        if existing:
            connection.execute(
                """
                UPDATE positions SET
                    quantity = ?, average_buy_price = ?, invested_amount = ?,
                    current_price = ?, current_value = ?, source = ?,
                    binance_asset = ?, synced_from_binance = ?, is_active = ?,
                    status = ?, last_sync_at = ?, realized_pnl = ?, unrealized_pnl = ?,
                    total_pnl = ?, profit_loss_pct = ?, is_estimated = ?,
                    market_source = ?, resolved_symbol = ?, base_asset = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    values["quantity"],
                    values["average_buy_price"],
                    values["invested_amount"],
                    values["current_price"],
                    values["current_value"],
                    values["source"],
                    values["binance_asset"],
                    values["synced_from_binance"],
                    values["is_active"],
                    values["status"],
                    values["last_sync_at"],
                    values["realized_pnl"],
                    values["unrealized_pnl"],
                    values["total_pnl"],
                    values["profit_loss_pct"],
                    values["is_estimated"],
                    values["market_source"],
                    values["resolved_symbol"],
                    values["base_asset"],
                    values["updated_at"],
                    existing["id"],
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO positions (
                    symbol, quantity, average_buy_price, invested_amount,
                    current_price, current_value, strategy_type, source,
                    binance_asset, synced_from_binance, is_active, status,
                    last_sync_at, realized_pnl, unrealized_pnl, total_pnl,
                    profit_loss_pct, is_estimated, market_source, resolved_symbol,
                    base_asset, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["symbol"],
                    values["quantity"],
                    values["average_buy_price"],
                    values["invested_amount"],
                    values["current_price"],
                    values["current_value"],
                    values["strategy_type"],
                    values["source"],
                    values["binance_asset"],
                    values["synced_from_binance"],
                    values["is_active"],
                    values["status"],
                    values["last_sync_at"],
                    values["realized_pnl"],
                    values["unrealized_pnl"],
                    values["total_pnl"],
                    values["profit_loss_pct"],
                    values["is_estimated"],
                    values["market_source"],
                    values["resolved_symbol"],
                    values["base_asset"],
                    now,
                    values["updated_at"],
                ),
            )
        connection.commit()


def cleanup_inactive_binance_positions(
    active_symbols: list[str],
    dust_symbols: list[str] | None = None,
    db_path: Path | str = DB_PATH,
) -> None:
    now = _now()
    protected_symbols = active_symbols + (dust_symbols or [])
    placeholders = ", ".join("?" for _ in protected_symbols)
    query = """
        UPDATE positions
        SET is_active = 0, status = 'CLOSED', updated_at = ?
        WHERE synced_from_binance = 1
    """
    params: list[Any] = [now]
    if protected_symbols:
        query += f" AND symbol NOT IN ({placeholders})"
        params.extend(protected_symbols)
    with _connect(db_path) as connection:
        connection.execute(query, params)
        connection.commit()


def sync_binance_positions(
    client: BinancePrivateClient | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    client = client or BinancePrivateClient()
    balances = sync_balances(client=client, db_path=db_path)
    now = _now()
    active_positions, dust = rebuild_active_positions_from_balances(
        balances,
        client=client,
        db_path=db_path,
    )
    active_symbols = [position["symbol"] for position in active_positions]
    dust_symbols = [item["symbol"] for item in dust]

    for position in active_positions:
        _upsert_synced_position(position, "ACTIVE", True, now, db_path=db_path)

    for item in dust:
        _upsert_synced_position(
            {
                "asset": item["asset"],
                "symbol": item["symbol"],
                "quantity": item["quantity"],
                "current_price": item["current_price"],
                "current_value": item["current_value_usdc"],
                "is_estimated": 1,
            },
            "DUST",
            False,
            now,
            db_path=db_path,
        )

    cleanup_inactive_binance_positions(active_symbols, dust_symbols, db_path=db_path)

    return {"positions": active_positions, "dust": dust}


def sync_positions_from_binance(
    client: BinancePrivateClient | None = None,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    return sync_binance_positions(client=client, db_path=db_path)["positions"]


def sync_binance_account(
    client: BinancePrivateClient | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    client = client or BinancePrivateClient()
    status = get_private_client_status()
    if not client.configured:
        return {
            **status,
            "synced": False,
            "message": "API Binance privée non configurée",
            "last_sync": get_last_sync(db_path=db_path),
        }

    positions = sync_positions_from_binance(client=client, db_path=db_path)
    last_sync = _now()
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO binance_sync_state (id, last_sync)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_sync = excluded.last_sync
            """,
            (last_sync,),
        )
        connection.commit()

    return {
        **status,
        "synced": True,
        "last_sync": last_sync,
        "positions_synced": len(positions),
        "active_threshold_usdc": MIN_ACTIVE_POSITION_VALUE_USDC,
    }


def get_last_sync(db_path: Path | str = DB_PATH) -> str | None:
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT last_sync FROM binance_sync_state WHERE id = 1"
        ).fetchone()
    return row["last_sync"] if row else None


def get_binance_status(db_path: Path | str = DB_PATH) -> dict[str, Any]:
    return {
        **get_private_client_status(),
        "last_sync": get_last_sync(db_path=db_path),
    }


def get_balances(db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    with _connect(db_path) as connection:
        rows = connection.execute(
            "SELECT asset, free, locked, total, updated_at FROM binance_balances WHERE total > 0 ORDER BY total DESC"
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_binance_positions(db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM positions
            WHERE synced_from_binance = 1
              AND is_active = 1
              AND status = 'ACTIVE'
              AND COALESCE(current_value, 0) >= ?
            ORDER BY symbol
            """,
            (MIN_ACTIVE_POSITION_VALUE_USDC,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_active_positions(db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM positions
            WHERE is_active = 1
              AND (
                (
                  source IN ('BINANCE', 'MIXED')
                  AND status = 'ACTIVE'
                  AND COALESCE(current_value, 0) >= ?
                )
                OR
                (
                  source = 'MANUAL'
                  AND COALESCE(status, 'MANUAL') IN ('MANUAL', 'ACTIVE')
                )
              )
            ORDER BY symbol
            """,
            (MIN_ACTIVE_POSITION_VALUE_USDC,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_inactive_positions(db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    with _connect(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM positions WHERE is_active = 0 ORDER BY updated_at DESC"
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_closed_trade_symbols(db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT t.symbol
            FROM binance_trades t
            LEFT JOIN positions p
              ON p.symbol = t.symbol
             AND p.synced_from_binance = 1
             AND p.is_active = 1
             AND p.status = 'ACTIVE'
             AND COALESCE(p.current_value, 0) >= ?
            WHERE p.id IS NULL
            ORDER BY t.symbol
            """,
            (MIN_ACTIVE_POSITION_VALUE_USDC,),
        ).fetchall()
    return [{"symbol": row["symbol"]} for row in rows]


def get_dust_balances(db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    dust: list[dict[str, Any]] = []
    with _connect(db_path) as connection:
        rows = connection.execute(
            "SELECT asset, total FROM binance_balances WHERE total > ? ORDER BY asset",
            (MIN_POSITION_AMOUNT,),
        ).fetchall()

    for row in rows:
        asset = row["asset"]
        if asset == QUOTE_ASSET:
            continue
        symbol = asset_to_symbol(asset)
        current_price = get_current_price(symbol)
        if current_price is None:
            continue
        current_value = float(row["total"]) * current_price
        if not is_active_position_value(current_value):
            dust.append(
                {
                    "asset": asset,
                    "symbol": symbol,
                    "quantity": round(float(row["total"]), 8),
                    "current_price": current_price,
                    "current_value_usdc": round(current_value, 8),
                    "reason": f"Valeur inférieure au seuil actif de {MIN_ACTIVE_POSITION_VALUE_USDC:g} USDC",
                }
            )
    return dust


def get_pnl_summary(db_path: Path | str = DB_PATH) -> dict[str, float]:
    with _connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT
                COALESCE(SUM(invested_amount), 0) AS total_invested,
                COALESCE(SUM(current_value), 0) AS current_value,
                COALESCE(SUM(unrealized_pnl), 0) AS unrealized_pnl,
                COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
                COALESCE(SUM(total_pnl), 0) AS total_pnl
            FROM positions
            WHERE synced_from_binance = 1
              AND is_active = 1
              AND status = 'ACTIVE'
              AND COALESCE(current_value, 0) >= ?
            """
            ,
            (MIN_ACTIVE_POSITION_VALUE_USDC,),
        ).fetchone()
        inactive_row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM positions
            WHERE synced_from_binance = 1 AND status = 'CLOSED'
            """
        ).fetchone()
        dust_row = connection.execute(
            """
            SELECT COUNT(*) AS count, COALESCE(SUM(current_value), 0) AS value
            FROM positions
            WHERE synced_from_binance = 1 AND status = 'DUST'
            """
        ).fetchone()
        usdc_row = connection.execute(
            """
            SELECT COALESCE(total, 0) AS total
            FROM binance_balances
            WHERE asset = 'USDC'
            """
        ).fetchone()
        manual_realized_row = connection.execute(
            """
            SELECT COALESCE(SUM(realized_pnl), 0) AS realized_pnl
            FROM positions
            WHERE COALESCE(synced_from_binance, 0) = 0
              AND source = 'MANUAL'
              AND status = 'CLOSED'
            """
        ).fetchone()

    total_invested = float(row["total_invested"])
    unrealized_pnl = float(row["unrealized_pnl"])
    realized_pnl = float(row["realized_pnl"]) + float(manual_realized_row["realized_pnl"])
    current_value = float(row["current_value"])
    usdc_available = float(usdc_row["total"]) if usdc_row else 0.0
    portfolio_value = current_value + usdc_available
    total_pnl = unrealized_pnl + realized_pnl
    active_positions = get_binance_positions(db_path=db_path)
    return {
        "total_invested": round(total_invested, 8),
        "current_value": round(current_value, 8),
        "usdc_available": round(usdc_available, 8),
        "portfolio_value_usdc": round(portfolio_value, 8),
        "portfolio_value_eur": round(portfolio_value * 0.92, 8),
        "usdc_to_eur_rate_estimate": 0.92,
        "unrealized_pnl": round(unrealized_pnl, 8),
        "realized_pnl": round(realized_pnl, 8),
        "total_pnl": round(total_pnl, 8),
        "active_positions_count": len(active_positions),
        "inactive_symbols_count": int(inactive_row["count"]),
        "closed_positions_count": int(inactive_row["count"]),
        "dust_count": int(dust_row["count"]),
        "dust_value_usdc": round(float(dust_row["value"]), 8),
        "active_threshold_usdc": MIN_ACTIVE_POSITION_VALUE_USDC,
        "is_estimated": any(
            bool(position.get("is_estimated"))
            for position in active_positions
        ),
    }


def get_account_summary(db_path: Path | str = DB_PATH) -> dict[str, Any]:
    pnl = get_pnl_summary(db_path=db_path)
    return {
        "active_value_usdc": pnl["current_value"],
        "dust_value_usdc": pnl["dust_value_usdc"],
        "active_positions_count": pnl["active_positions_count"],
        "dust_count": pnl["dust_count"],
        "closed_positions_count": pnl["closed_positions_count"],
        "active_threshold_usdc": MIN_ACTIVE_POSITION_VALUE_USDC,
    }
