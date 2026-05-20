from __future__ import annotations

from typing import Any

from services.binance_alpha import (
    MARKET_SOURCE_ALPHA,
    MARKET_SOURCE_MANUAL,
    MARKET_SOURCE_SPOT,
    MARKET_SOURCE_UNKNOWN,
)
from services.binance_sync import MIN_ACTIVE_POSITION_VALUE_USDC


BADGE_PRIORITY = ["Détenue", "Surveillance", "Opportunité", "Watchlist", "Spot", "Alpha", "Manuel", "Inconnu"]
SOURCE_PRIORITY = [
    "POSITION",
    MARKET_SOURCE_SPOT,
    MARKET_SOURCE_ALPHA,
    MARKET_SOURCE_MANUAL,
    MARKET_SOURCE_UNKNOWN,
    "BINANCE",
    "MIXED",
    "WATCH_CANDIDATE",
    "OPPORTUNITY_SCANNER",
    "WATCHLIST",
]


def _is_truthy(value: Any) -> bool:
    return bool(int(value)) if isinstance(value, (int, bool)) else bool(value)


def is_active_position(position: dict[str, Any]) -> bool:
    if not _is_truthy(position.get("is_active", 1)):
        return False
    status = position.get("status", "MANUAL")
    source = position.get("source", "MANUAL")
    if source in {"BINANCE", "MIXED"}:
        return status == "ACTIVE" and float(position.get("current_value") or 0) >= MIN_ACTIVE_POSITION_VALUE_USDC
    return status in {"MANUAL", "ACTIVE"}


def _entry(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "sources": [],
        "badges": [],
        "priority": 99,
        "position_info": None,
        "watch_info": None,
        "opportunity_info": None,
    }


def _source_badge(market_source: str | None, fallback_source: str | None = None) -> str:
    source = str(market_source or "").upper()
    fallback = str(fallback_source or "").upper()
    if source == MARKET_SOURCE_ALPHA:
        return "Alpha"
    if source == MARKET_SOURCE_SPOT or fallback in {"BINANCE", "MIXED"}:
        return "Spot"
    if source == MARKET_SOURCE_MANUAL or fallback == "MANUAL":
        return "Manuel"
    return "Inconnu"


def _source_code(market_source: str | None, fallback_source: str | None = None, symbol: str | None = None) -> str:
    source = str(market_source or "").upper()
    fallback = str(fallback_source or "").upper()
    if source in {MARKET_SOURCE_SPOT, MARKET_SOURCE_ALPHA, MARKET_SOURCE_MANUAL, MARKET_SOURCE_UNKNOWN}:
        return source
    if fallback in {"BINANCE", "MIXED"}:
        return MARKET_SOURCE_SPOT
    if fallback == "MANUAL":
        return MARKET_SOURCE_MANUAL
    if str(symbol or "").upper().endswith("USDC"):
        return MARKET_SOURCE_SPOT
    return MARKET_SOURCE_UNKNOWN


def _add_badge(item: dict[str, Any], badge: str) -> None:
    if badge not in item["badges"]:
        item["badges"].append(badge)
        item["badges"].sort(key=lambda value: BADGE_PRIORITY.index(value) if value in BADGE_PRIORITY else 99)


def _add_source(item: dict[str, Any], source: str) -> None:
    if source not in item["sources"]:
        item["sources"].append(source)
        item["sources"].sort(key=lambda value: SOURCE_PRIORITY.index(value) if value in SOURCE_PRIORITY else 99)


def _position_info(position: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": position.get("id"),
        "quantity": position.get("quantity"),
        "average_buy_price": position.get("average_buy_price"),
        "current_price": position.get("current_price"),
        "current_value": position.get("current_value"),
        "unrealized_pnl_pct": position.get("profit_loss_pct", position.get("gain_loss_pct")),
        "unrealized_pnl": position.get("unrealized_pnl", position.get("gain_loss_amount")),
        "realized_pnl": position.get("realized_pnl"),
        "synced_from_binance": position.get("synced_from_binance"),
        "source": position.get("source", "MANUAL"),
        "market_source": _source_code(position.get("market_source"), position.get("source"), position.get("symbol")),
        "resolved_symbol": position.get("resolved_symbol"),
        "base_asset": position.get("base_asset"),
        "status": position.get("status"),
        "is_valid_symbol": position.get("is_valid_symbol", True),
        "analysis_available": position.get("analysis_available", True),
        "market_error": position.get("market_error"),
        "global_score": position.get("global_score") or position.get("score"),
        "global_trend": position.get("global_trend") or position.get("trend_score"),
        "setup_quality": position.get("setup_quality"),
        "price_change_pct": position.get("price_change_pct"),
        "quote_volume": position.get("quote_volume"),
        "market_cap": position.get("market_cap"),
        "alpha_ma7": position.get("alpha_ma7"),
        "alpha_ma25": position.get("alpha_ma25"),
        "alpha_ma99": position.get("alpha_ma99"),
        "alpha_momentum_pct": position.get("alpha_momentum_pct"),
        "candles_available": position.get("candles_available", False),
        "limited_analysis": position.get("limited_analysis", False),
    }


def _watch_info(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": candidate.get("id"),
        "target_buy_price": candidate.get("target_buy_price"),
        "distance_to_target_pct": candidate.get("distance_to_target_buy_pct"),
        "priority": candidate.get("priority"),
        "reason": candidate.get("reason"),
        "note": candidate.get("note"),
        "current_price": candidate.get("current_price"),
        "market_source": _source_code(candidate.get("market_source"), symbol=candidate.get("symbol")),
        "resolved_symbol": candidate.get("resolved_symbol"),
        "base_asset": candidate.get("base_asset"),
        "analysis_available": candidate.get("analysis_available", True),
        "market_error": candidate.get("market_error"),
        "global_score": candidate.get("global_score") or candidate.get("score"),
        "global_trend": candidate.get("global_trend"),
        "setup_quality": candidate.get("setup_quality"),
        "price_change_pct": candidate.get("price_change_pct"),
        "quote_volume": candidate.get("quote_volume"),
        "market_cap": candidate.get("market_cap"),
        "alpha_ma7": candidate.get("alpha_ma7"),
        "alpha_ma25": candidate.get("alpha_ma25"),
        "alpha_ma99": candidate.get("alpha_ma99"),
        "alpha_momentum_pct": candidate.get("alpha_momentum_pct"),
        "candles_available": candidate.get("candles_available", False),
        "limited_analysis": candidate.get("limited_analysis", False),
    }


def _opportunity_info(opportunity: dict[str, Any]) -> dict[str, Any]:
    return {
        "scan_score": opportunity.get("scan_score"),
        "reason": opportunity.get("reason"),
        "price_change_pct": opportunity.get("price_change_pct"),
        "volatility_pct": opportunity.get("volatility_pct"),
    }


def merge_symbols_from_sources(
    *,
    positions: list[dict[str, Any]] | None = None,
    watch_candidates: list[dict[str, Any]] | None = None,
    watchlist: list[str] | None = None,
    opportunities: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for position in positions or []:
        if not is_active_position(position):
            continue
        symbol = position["symbol"].upper()
        item = merged.setdefault(symbol, _entry(symbol))
        market_source = _source_code(position.get("market_source"), position.get("source"), position.get("symbol"))
        _add_source(item, "POSITION")
        _add_source(item, market_source)
        _add_badge(item, "Détenue")
        _add_badge(item, _source_badge(market_source, position.get("source")))
        item["priority"] = min(item["priority"], 1)
        item["position_info"] = _position_info(position)

    for candidate in watch_candidates or []:
        symbol = candidate["symbol"].upper()
        item = merged.setdefault(symbol, _entry(symbol))
        market_source = _source_code(candidate.get("market_source"), symbol=candidate.get("symbol"))
        _add_source(item, "WATCH_CANDIDATE")
        _add_source(item, market_source)
        _add_badge(item, "Surveillance")
        _add_badge(item, _source_badge(market_source))
        priority = 2 if candidate.get("priority") == "HIGH" else 3
        item["priority"] = min(item["priority"], priority)
        item["watch_info"] = _watch_info(candidate)

    for opportunity in opportunities or []:
        symbol = str(opportunity.get("symbol", "")).upper()
        if not symbol:
            continue
        item = merged.setdefault(symbol, _entry(symbol))
        _add_source(item, "OPPORTUNITY_SCANNER")
        _add_source(item, MARKET_SOURCE_SPOT)
        _add_badge(item, "Opportunité")
        _add_badge(item, "Spot")
        item["priority"] = min(item["priority"], 4)
        item["opportunity_info"] = _opportunity_info(opportunity)

    for symbol in watchlist or []:
        upper = symbol.upper()
        item = merged.setdefault(upper, _entry(upper))
        _add_source(item, "WATCHLIST")
        _add_source(item, MARKET_SOURCE_SPOT)
        _add_badge(item, "Watchlist")
        _add_badge(item, "Spot")
        item["priority"] = min(item["priority"], 5)

    return sorted(merged.values(), key=lambda item: (item["priority"], item["symbol"]))


def get_symbol_sources(symbol: str, universe: list[dict[str, Any]]) -> list[str]:
    upper = symbol.upper()
    for item in universe:
        if item["symbol"] == upper:
            return item["sources"]
    return []


def get_market_universe(
    *,
    positions: list[dict[str, Any]] | None = None,
    watch_candidates: list[dict[str, Any]] | None = None,
    watchlist: list[str] | None = None,
    opportunities: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    return merge_symbols_from_sources(
        positions=positions,
        watch_candidates=watch_candidates,
        watchlist=watchlist,
        opportunities=opportunities,
    )
