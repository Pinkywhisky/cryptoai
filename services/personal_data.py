import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.history import DB_PATH, init_db
from services.binance_sync import MIN_ACTIVE_POSITION_VALUE_USDC
from services.binance_alpha import (
    MARKET_SOURCE_ALPHA,
    MARKET_SOURCE_MANUAL,
    MARKET_SOURCE_SPOT,
    MARKET_SOURCE_UNKNOWN,
    get_alpha_market_data,
)
from services.symbols import resolve_market_symbol


POSITION_FIELDS = [
    "symbol",
    "quantity",
    "average_buy_price",
    "invested_amount",
    "strategy_type",
    "take_profit_1",
    "take_profit_2",
    "stop_loss",
    "max_loss_accepted_pct",
    "note",
    "source",
    "status",
    "market_source",
    "resolved_symbol",
    "base_asset",
]
WATCH_FIELDS = [
    "symbol",
    "target_buy_price",
    "invalidation_price",
    "priority",
    "reason",
    "note",
    "market_source",
    "resolved_symbol",
    "base_asset",
]
JOURNAL_FIELDS = [
    "symbol",
    "action",
    "reason",
    "confidence",
    "emotion",
    "result",
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    init_db(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _clean_payload(payload: dict[str, Any], allowed_fields: list[str]) -> dict[str, Any]:
    return {field: payload.get(field) for field in allowed_fields if field in payload}


def resolve_user_market_symbol(payload: dict[str, Any]) -> dict[str, Any]:
    raw_symbol = str(payload.get("symbol") or "").strip().upper()
    if not raw_symbol:
        raise ValueError("Symbole invalide. Exemple attendu : BTCUSDC ou BTC.")
    result = resolve_market_symbol(raw_symbol)
    if not result.get("valid"):
        raise ValueError("Symbole introuvable sur Binance Spot et Binance Alpha.")
    return result


def normalize_user_symbol(payload: dict[str, Any]) -> str:
    return resolve_user_market_symbol(payload)["symbol"]


def resolve_position_market_symbol(payload: dict[str, Any], existing_source: str | None = None) -> dict[str, Any]:
    raw_symbol = str(payload.get("symbol") or "").strip().upper()
    if not raw_symbol:
        raise ValueError("Symbole invalide. Exemple attendu : BTCUSDC ou BTC.")
    source = (payload.get("source") or existing_source or "MANUAL").upper()
    result = resolve_market_symbol(raw_symbol)
    if result.get("valid"):
        return result
    if source == "MANUAL":
        return {
            "valid": True,
            "symbol": raw_symbol,
            "resolved_symbol": raw_symbol,
            "base_asset": raw_symbol,
            "quote_asset": None,
            "market_source": MARKET_SOURCE_MANUAL,
        }
    raise ValueError("Symbole introuvable sur Binance Spot et Binance Alpha.")


def normalize_position_symbol(payload: dict[str, Any], existing_source: str | None = None) -> str:
    return resolve_position_market_symbol(payload, existing_source=existing_source)["symbol"]


def _watch_candidate_exists(
    connection: sqlite3.Connection,
    symbol: str,
    exclude_id: int | None = None,
) -> bool:
    if exclude_id is None:
        row = connection.execute(
            "SELECT id FROM watch_candidates WHERE symbol = ? LIMIT 1",
            (symbol,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT id FROM watch_candidates WHERE symbol = ? AND id != ? LIMIT 1",
            (symbol, exclude_id),
        ).fetchone()
    return row is not None


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator * 100, 4)


def get_market_snapshot(symbol: str) -> dict[str, Any]:
    from services.scanner import analyze_symbol_multi

    raw_symbol = str(symbol or "").strip().upper()
    resolution = resolve_market_symbol(raw_symbol)
    if not resolution.get("valid"):
        return {
            "symbol": raw_symbol,
            "is_valid_symbol": False,
            "analysis_available": False,
            "market_source": MARKET_SOURCE_UNKNOWN,
            "market_error": "Symbole Binance invalide",
        }

    if resolution.get("market_source") == MARKET_SOURCE_ALPHA:
        try:
            alpha_data = get_alpha_market_data(resolution["symbol"])
        except Exception:
            alpha_data = {"valid": False}
        if alpha_data.get("valid"):
            return {
                **alpha_data,
                "is_valid_symbol": True,
                "analysis_available": False,
                "price_available": alpha_data.get("current_price") is not None,
            }
        return {
            "symbol": resolution["symbol"],
            "is_valid_symbol": True,
            "analysis_available": False,
            "market_source": MARKET_SOURCE_ALPHA,
            "market_error": "Prix Alpha indisponible",
        }

    normalized = resolution["symbol"]
    try:
        snapshot = analyze_symbol_multi(symbol=normalized, limit=120)
    except Exception as exc:
        return {
            "symbol": normalized,
            "is_valid_symbol": True,
            "analysis_available": False,
            "market_error": str(exc),
        }

    snapshot["is_valid_symbol"] = True
    snapshot["analysis_available"] = True
    snapshot["market_source"] = MARKET_SOURCE_SPOT
    return snapshot


def calculate_trend_score(snapshot: dict[str, Any]) -> str:
    global_score = snapshot.get("global_score", 0)
    score_4h = snapshot.get("timeframes", {}).get("4h", {}).get("score", global_score)
    score_1d = snapshot.get("timeframes", {}).get("1d", {}).get("score", global_score)

    if global_score < 35 or (score_4h < 40 and score_1d < 40):
        return "DOWN STRONG"
    if global_score < 45:
        return "DOWN"
    if global_score >= 70:
        return "UP STRONG"
    if global_score >= 55:
        return "UP"
    return "NEUTRAL"


def _distance_to_level(current_price: float | None, level: float | None) -> float | None:
    if current_price is None or level is None or current_price <= 0:
        return None
    return round((level - current_price) / current_price * 100, 4)


def enrich_position(position: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(position)
    snapshot = get_market_snapshot(position["symbol"])
    if not snapshot.get("analysis_available", True):
        current_price = snapshot.get("current_price")
        quantity = float(position.get("quantity") or 0)
        invested_amount = float(position.get("invested_amount") or 0)
        current_value = current_price * quantity if current_price is not None else None
        gain_loss_amount = (
            round(current_value - invested_amount, 6)
            if current_value is not None
            else None
        )
        enriched.update(
            {
                "is_valid_symbol": snapshot.get("is_valid_symbol", False),
                "analysis_available": False,
                "limited_analysis": snapshot.get("limited_analysis", False),
                "price_available": snapshot.get("price_available", False),
                "market_source": snapshot.get("market_source"),
                "market_error": snapshot.get("market_error", "Analyse indisponible"),
                "current_price": current_price,
                "current_value": round(current_value, 6) if current_value is not None else None,
                "gain_loss_amount": gain_loss_amount,
                "gain_loss_pct": _pct(gain_loss_amount, invested_amount),
                "global_score": snapshot.get("global_score"),
                "global_trend": snapshot.get("global_trend"),
                "setup_quality": snapshot.get("setup_quality"),
                "price_change_pct": snapshot.get("price_change_pct"),
                "quote_volume": snapshot.get("quote_volume"),
                "market_cap": snapshot.get("market_cap"),
                "alpha_ma7": snapshot.get("alpha_ma7"),
                "alpha_ma25": snapshot.get("alpha_ma25"),
                "alpha_ma99": snapshot.get("alpha_ma99"),
                "alpha_momentum_pct": snapshot.get("alpha_momentum_pct"),
                "candles_available": snapshot.get("candles_available", False),
                "signal": None,
                "score": None,
                "trend_score": snapshot.get("global_trend", "UNKNOWN"),
            }
        )
        return enriched

    current_price = snapshot.get("current_price")
    quantity = float(position["quantity"])
    invested_amount = float(position["invested_amount"])
    average_buy_price = float(position["average_buy_price"])

    current_value = current_price * quantity if current_price is not None else None
    gain_loss_amount = (
        round(current_value - invested_amount, 6)
        if current_value is not None
        else None
    )
    gain_loss_pct = _pct(gain_loss_amount, invested_amount)

    enriched.update(
        {
            "current_price": current_price,
            "current_value": round(current_value, 6) if current_value is not None else None,
            "market_source": snapshot.get("market_source"),
            "limited_analysis": snapshot.get("limited_analysis", False),
            "gain_loss_amount": gain_loss_amount,
            "gain_loss_pct": gain_loss_pct,
            "global_score": snapshot.get("global_score"),
            "global_trend": snapshot.get("global_trend"),
            "setup_quality": snapshot.get("setup_quality"),
            "price_change_pct": snapshot.get("price_change_pct"),
            "quote_volume": snapshot.get("quote_volume"),
            "market_cap": snapshot.get("market_cap"),
            "alpha_ma7": snapshot.get("alpha_ma7"),
            "alpha_ma25": snapshot.get("alpha_ma25"),
            "alpha_ma99": snapshot.get("alpha_ma99"),
            "alpha_momentum_pct": snapshot.get("alpha_momentum_pct"),
            "candles_available": snapshot.get("candles_available", False),
            "distance_to_break_even_pct": _distance_to_level(current_price, average_buy_price),
            "distance_to_tp1_pct": _distance_to_level(
                current_price,
                position.get("take_profit_1"),
            ),
            "distance_to_tp2_pct": _distance_to_level(
                current_price,
                position.get("take_profit_2"),
            ),
            "distance_to_stop_loss_pct": _distance_to_level(
                current_price,
                position.get("stop_loss"),
            ),
            "signal": snapshot.get("global_signal"),
            "score": snapshot.get("global_score"),
            "trend_score": calculate_trend_score(snapshot),
        }
    )
    return enriched


def list_positions(db_path: Path | str = DB_PATH, enrich: bool = True) -> list[dict[str, Any]]:
    with _connect(db_path) as connection:
        rows = connection.execute("SELECT * FROM positions ORDER BY id DESC").fetchall()
    positions = [_row_to_dict(row) for row in rows]
    return [enrich_position(position) for position in positions] if enrich else positions


def create_position(payload: dict[str, Any], db_path: Path | str = DB_PATH) -> dict[str, Any]:
    data = _clean_payload(payload, POSITION_FIELDS)
    data["source"] = data.get("source") or "MANUAL"
    resolution = resolve_position_market_symbol(data)
    data["symbol"] = resolution["symbol"]
    data["resolved_symbol"] = resolution.get("resolved_symbol") or resolution["symbol"]
    data["base_asset"] = resolution.get("base_asset")
    data["market_source"] = resolution.get("market_source")
    data["status"] = data.get("status") or ("MANUAL" if data["source"] == "MANUAL" else "ACTIVE")
    data["synced_from_binance"] = 1 if data["source"] == "BINANCE" else 0
    now = _now()
    data.update({"created_at": now, "updated_at": now})

    fields = list(data.keys())
    placeholders = ", ".join("?" for _ in fields)
    with _connect(db_path) as connection:
        cursor = connection.execute(
            f"INSERT INTO positions ({', '.join(fields)}) VALUES ({placeholders})",
            [data[field] for field in fields],
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM positions WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return _row_to_dict(row)


def update_position(
    position_id: int,
    payload: dict[str, Any],
    db_path: Path | str = DB_PATH,
) -> dict[str, Any] | None:
    data = _clean_payload(payload, POSITION_FIELDS)
    with _connect(db_path) as connection:
        existing = connection.execute(
            "SELECT * FROM positions WHERE id = ?",
            (position_id,),
        ).fetchone()
        if not existing:
            return None

        if existing["synced_from_binance"]:
            for protected_field in [
                "symbol",
                "quantity",
                "average_buy_price",
                "invested_amount",
                "source",
                "status",
            ]:
                data.pop(protected_field, None)

        if "symbol" in data:
            resolution = resolve_position_market_symbol(data, existing_source=existing["source"])
            data["symbol"] = resolution["symbol"]
            data["resolved_symbol"] = resolution.get("resolved_symbol") or resolution["symbol"]
            data["base_asset"] = resolution.get("base_asset")
            data["market_source"] = resolution.get("market_source")
        data["updated_at"] = _now()
        assignments = ", ".join(f"{field} = ?" for field in data)
        connection.execute(
            f"UPDATE positions SET {assignments} WHERE id = ?",
            [data[field] for field in data] + [position_id],
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM positions WHERE id = ?",
            (position_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def delete_position(position_id: int, db_path: Path | str = DB_PATH) -> bool:
    with _connect(db_path) as connection:
        cursor = connection.execute("DELETE FROM positions WHERE id = ?", (position_id,))
        connection.commit()
    return cursor.rowcount > 0


def enrich_watch_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(candidate)
    snapshot = get_market_snapshot(candidate["symbol"])
    if not snapshot.get("analysis_available", True):
        enriched.update(
            {
                "is_valid_symbol": snapshot.get("is_valid_symbol", False),
                "analysis_available": False,
                "limited_analysis": snapshot.get("market_source") == MARKET_SOURCE_ALPHA,
                "price_available": snapshot.get("price_available", False),
                "market_source": snapshot.get("market_source") or candidate.get("market_source"),
                "market_error": snapshot.get("market_error", "Analyse indisponible"),
                "current_price": snapshot.get("current_price"),
                "global_score": snapshot.get("global_score"),
                "global_trend": snapshot.get("global_trend"),
                "setup_quality": snapshot.get("setup_quality"),
                "price_change_pct": snapshot.get("price_change_pct"),
                "quote_volume": snapshot.get("quote_volume"),
                "market_cap": snapshot.get("market_cap"),
                "alpha_ma7": snapshot.get("alpha_ma7"),
                "alpha_ma25": snapshot.get("alpha_ma25"),
                "alpha_ma99": snapshot.get("alpha_ma99"),
                "alpha_momentum_pct": snapshot.get("alpha_momentum_pct"),
                "candles_available": snapshot.get("candles_available", False),
                "distance_to_target_buy_pct": (
                    round((snapshot.get("current_price") - candidate.get("target_buy_price")) / snapshot.get("current_price") * 100, 4)
                    if snapshot.get("current_price") and candidate.get("target_buy_price")
                    else None
                ),
                "signal": None,
                "score": None,
            }
        )
        return enriched

    current_price = snapshot.get("current_price")
    target_buy_price = candidate.get("target_buy_price")

    distance_to_target = None
    if current_price and target_buy_price:
        distance_to_target = round((current_price - target_buy_price) / current_price * 100, 4)

    enriched.update(
        {
            "current_price": current_price,
            "market_source": snapshot.get("market_source") or candidate.get("market_source"),
            "limited_analysis": snapshot.get("limited_analysis", False),
            "distance_to_target_buy_pct": distance_to_target,
            "signal": snapshot.get("global_signal"),
            "score": snapshot.get("global_score"),
            "global_score": snapshot.get("global_score"),
            "global_trend": snapshot.get("global_trend"),
            "setup_quality": snapshot.get("setup_quality"),
            "price_change_pct": snapshot.get("price_change_pct"),
            "quote_volume": snapshot.get("quote_volume"),
            "market_cap": snapshot.get("market_cap"),
            "alpha_ma7": snapshot.get("alpha_ma7"),
            "alpha_ma25": snapshot.get("alpha_ma25"),
            "alpha_ma99": snapshot.get("alpha_ma99"),
            "alpha_momentum_pct": snapshot.get("alpha_momentum_pct"),
            "candles_available": snapshot.get("candles_available", False),
        }
    )
    return enriched


def list_watch_candidates(
    db_path: Path | str = DB_PATH,
    enrich: bool = True,
) -> list[dict[str, Any]]:
    with _connect(db_path) as connection:
        rows = connection.execute("SELECT * FROM watch_candidates ORDER BY id DESC").fetchall()
    candidates = [_row_to_dict(row) for row in rows]
    return [enrich_watch_candidate(candidate) for candidate in candidates] if enrich else candidates


def create_watch_candidate(payload: dict[str, Any], db_path: Path | str = DB_PATH) -> dict[str, Any]:
    data = _clean_payload(payload, WATCH_FIELDS)
    resolution = resolve_user_market_symbol(data)
    data["symbol"] = resolution["symbol"]
    data["resolved_symbol"] = resolution.get("resolved_symbol") or resolution["symbol"]
    data["base_asset"] = resolution.get("base_asset")
    data["market_source"] = resolution.get("market_source")
    data["priority"] = data.get("priority", "MEDIUM")
    now = _now()
    data.update({"created_at": now, "updated_at": now})

    fields = list(data.keys())
    placeholders = ", ".join("?" for _ in fields)
    with _connect(db_path) as connection:
        if _watch_candidate_exists(connection, data["symbol"]):
            raise ValueError("Symbol already exists in watchlist")
        cursor = connection.execute(
            f"INSERT INTO watch_candidates ({', '.join(fields)}) VALUES ({placeholders})",
            [data[field] for field in fields],
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM watch_candidates WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return _row_to_dict(row)


def update_watch_candidate(
    candidate_id: int,
    payload: dict[str, Any],
    db_path: Path | str = DB_PATH,
) -> dict[str, Any] | None:
    data = _clean_payload(payload, WATCH_FIELDS)
    if "symbol" in data:
        resolution = resolve_user_market_symbol(data)
        data["symbol"] = resolution["symbol"]
        data["resolved_symbol"] = resolution.get("resolved_symbol") or resolution["symbol"]
        data["base_asset"] = resolution.get("base_asset")
        data["market_source"] = resolution.get("market_source")
    data["updated_at"] = _now()

    with _connect(db_path) as connection:
        existing = connection.execute(
            "SELECT * FROM watch_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if not existing:
            return None
        if "symbol" in data and _watch_candidate_exists(connection, data["symbol"], exclude_id=candidate_id):
            raise ValueError("Symbol already exists in watchlist")
        assignments = ", ".join(f"{field} = ?" for field in data)
        connection.execute(
            f"UPDATE watch_candidates SET {assignments} WHERE id = ?",
            [data[field] for field in data] + [candidate_id],
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM watch_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def delete_watch_candidate(candidate_id: int, db_path: Path | str = DB_PATH) -> bool:
    with _connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM watch_candidates WHERE id = ?",
            (candidate_id,),
        )
        connection.commit()
    return cursor.rowcount > 0


def cleanup_invalid_symbols(db_path: Path | str = DB_PATH) -> dict[str, Any]:
    invalid_symbols: list[str] = []
    removed_watch = 0
    marked_positions = 0

    with _connect(db_path) as connection:
        watch_rows = connection.execute("SELECT id, symbol FROM watch_candidates").fetchall()
        for row in watch_rows:
            symbol = str(row["symbol"]).upper()
            if not resolve_market_symbol(symbol).get("valid"):
                invalid_symbols.append(symbol)
                cursor = connection.execute("DELETE FROM watch_candidates WHERE id = ?", (row["id"],))
                removed_watch += cursor.rowcount

        position_rows = connection.execute("SELECT id, symbol, source FROM positions").fetchall()
        for row in position_rows:
            symbol = str(row["symbol"]).upper()
            if not resolve_market_symbol(symbol).get("valid"):
                invalid_symbols.append(symbol)
                if str(row["source"] or "MANUAL").upper() == "MANUAL":
                    continue
                cursor = connection.execute(
                    """
                    UPDATE positions
                    SET is_active = 0, status = 'INVALID', updated_at = ?
                    WHERE id = ?
                    """,
                    (_now(), row["id"]),
                )
                marked_positions += cursor.rowcount

        connection.commit()

    return {
        "removed": removed_watch + marked_positions,
        "watch_removed": removed_watch,
        "positions_marked_invalid": marked_positions,
        "invalid_symbols": sorted(set(invalid_symbols)),
    }


def repair_market_sources(db_path: Path | str = DB_PATH) -> dict[str, Any]:
    updated = 0
    symbols: list[str] = []

    with _connect(db_path) as connection:
        watch_rows = connection.execute("SELECT id, symbol, market_source FROM watch_candidates").fetchall()
        for row in watch_rows:
            resolution = resolve_market_symbol(row["symbol"])
            new_source = resolution.get("market_source", MARKET_SOURCE_UNKNOWN)
            if row["market_source"] != new_source:
                connection.execute(
                    """
                    UPDATE watch_candidates
                    SET symbol = ?, resolved_symbol = ?, base_asset = ?, market_source = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        resolution.get("symbol") or row["symbol"],
                        resolution.get("resolved_symbol") or resolution.get("symbol") or row["symbol"],
                        resolution.get("base_asset"),
                        new_source,
                        _now(),
                        row["id"],
                    ),
                )
                updated += 1
                symbols.append(str(row["symbol"]).upper())

        position_rows = connection.execute("SELECT id, symbol, source, market_source FROM positions").fetchall()
        for row in position_rows:
            source = str(row["source"] or "MANUAL").upper()
            resolution = resolve_market_symbol(row["symbol"])
            if not resolution.get("valid") and source == "MANUAL":
                resolution = {
                    "symbol": str(row["symbol"]).upper(),
                    "resolved_symbol": str(row["symbol"]).upper(),
                    "base_asset": str(row["symbol"]).upper(),
                    "market_source": MARKET_SOURCE_MANUAL,
                }
            new_source = resolution.get("market_source", MARKET_SOURCE_UNKNOWN)
            if row["market_source"] != new_source:
                connection.execute(
                    """
                    UPDATE positions
                    SET symbol = ?, resolved_symbol = ?, base_asset = ?, market_source = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        resolution.get("symbol") or row["symbol"],
                        resolution.get("resolved_symbol") or resolution.get("symbol") or row["symbol"],
                        resolution.get("base_asset"),
                        new_source,
                        _now(),
                        row["id"],
                    ),
                )
                updated += 1
                symbols.append(str(row["symbol"]).upper())

        connection.commit()

    return {"updated": updated, "symbols": sorted(set(symbols))}


def list_journal(db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    with _connect(db_path) as connection:
        rows = connection.execute("SELECT * FROM trade_journal ORDER BY id DESC").fetchall()
    return [_row_to_dict(row) for row in rows]


def create_journal_entry(payload: dict[str, Any], db_path: Path | str = DB_PATH) -> dict[str, Any]:
    data = _clean_payload(payload, JOURNAL_FIELDS)
    data["symbol"] = data["symbol"].upper()
    data["created_at"] = _now()

    fields = list(data.keys())
    placeholders = ", ".join("?" for _ in fields)
    with _connect(db_path) as connection:
        cursor = connection.execute(
            f"INSERT INTO trade_journal ({', '.join(fields)}) VALUES ({placeholders})",
            [data[field] for field in fields],
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM trade_journal WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return _row_to_dict(row)


def delete_journal_entry(entry_id: int, db_path: Path | str = DB_PATH) -> bool:
    with _connect(db_path) as connection:
        cursor = connection.execute("DELETE FROM trade_journal WHERE id = ?", (entry_id,))
        connection.commit()
    return cursor.rowcount > 0


def generate_position_alerts(positions: list[dict[str, Any]]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []

    for position in positions:
        source = position.get("source", "MANUAL")
        status = position.get("status", "MANUAL")
        is_binance_position = source in {"BINANCE", "MIXED"}
        current_value = float(position.get("current_value") or 0)
        if not position.get("is_active", 1):
            continue
        if is_binance_position and (
            status != "ACTIVE" or current_value < MIN_ACTIVE_POSITION_VALUE_USDC
        ):
            continue
        symbol = position["symbol"]

        for field, title in [
            ("distance_to_tp1_pct", "Proche TP1"),
            ("distance_to_tp2_pct", "Proche TP2"),
            ("distance_to_stop_loss_pct", "Proche stop-loss"),
        ]:
            distance = position.get(field)
            if distance is not None and abs(distance) <= 1:
                alerts.append(
                    {
                        "type": "portfolio",
                        "symbol": symbol,
                        "title": title,
                        "message": "Le prix est à moins de 1% du niveau défini.",
                        "severity": "HIGH" if "stop" in field else "MEDIUM",
                    }
                )

        gain_loss_pct = position.get("gain_loss_pct")
        max_loss = position.get("max_loss_accepted_pct")
        if gain_loss_pct is not None and max_loss is not None and gain_loss_pct <= -abs(max_loss):
            alerts.append(
                {
                    "type": "portfolio",
                    "symbol": symbol,
                    "title": "Perte maximale atteinte",
                    "message": "La perte dépasse le seuil accepté pour cette position.",
                    "severity": "HIGH",
                }
            )

        if gain_loss_pct is not None and gain_loss_pct >= 5:
            alerts.append(
                {
                    "type": "portfolio",
                    "symbol": symbol,
                    "title": "Gain supérieur à 5%",
                    "message": "La position dépasse +5% de performance.",
                    "severity": "LOW",
                }
            )
        elif gain_loss_pct is not None and gain_loss_pct >= 2:
            alerts.append(
                {
                    "type": "portfolio",
                    "symbol": symbol,
                    "title": "Gain supérieur à 2%",
                    "message": "La position dépasse +2% de performance.",
                    "severity": "LOW",
                }
            )

        if position.get("signal") == "ÉVITER / VENTE POSSIBLE":
            alerts.append(
                {
                    "type": "portfolio",
                    "symbol": symbol,
                    "title": "Signal négatif",
                    "message": "Le signal actuel est ÉVITER / VENTE POSSIBLE.",
                    "severity": "HIGH",
                }
            )

        if position.get("trend_score") == "DOWN STRONG":
            alerts.append(
                {
                    "type": "portfolio",
                    "symbol": symbol,
                    "title": "Trend score DOWN STRONG",
                    "message": "La tendance récente se dégrade fortement.",
                    "severity": "HIGH",
                }
            )

        break_even_distance = position.get("distance_to_break_even_pct")
        if break_even_distance is not None and abs(break_even_distance) <= 1:
            alerts.append(
                {
                    "type": "portfolio",
                    "symbol": symbol,
                    "title": "Proche break-even",
                    "message": "La position revient proche du prix moyen. Surveiller une sortie partielle si le momentum faiblit.",
                    "severity": "MEDIUM",
                }
            )

    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    alerts.sort(key=lambda alert: severity_order.get(alert["severity"], 99))
    return alerts


def generate_watch_candidate_alerts(candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []

    for candidate in candidates:
        symbol = candidate["symbol"]
        distance = candidate.get("distance_to_target_buy_pct")
        current_price = candidate.get("current_price")
        invalidation = candidate.get("invalidation_price")

        if distance is not None and abs(distance) <= 2:
            alerts.append(
                {
                    "type": "watch",
                    "symbol": symbol,
                    "title": "Prix visé proche",
                    "message": "La crypto surveillée approche du prix d'achat visé.",
                    "severity": "MEDIUM",
                }
            )

        if current_price is not None and invalidation is not None and current_price <= invalidation:
            alerts.append(
                {
                    "type": "watch",
                    "symbol": symbol,
                    "title": "Idée invalidée",
                    "message": "Le prix a cassé le niveau d'invalidation défini.",
                    "severity": "HIGH",
                }
            )

    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    alerts.sort(key=lambda alert: severity_order.get(alert["severity"], 99))
    return alerts


def generate_all_alerts(
    positions: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, str]]:
    alerts = generate_position_alerts(positions) + generate_watch_candidate_alerts(candidates)
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    alerts.sort(key=lambda alert: severity_order.get(alert["severity"], 99))
    return alerts
