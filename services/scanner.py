from typing import Any
from datetime import UTC, datetime

from services.binance_client import get_klines
from services.advanced_triggers import detect_advanced_triggers
from services.decision_engine import build_decision
from services.history import save_scan_result
from services.indicators import add_indicators
from services.scoring import analyze_market, get_signal
from services.trend_strength import trend_from_score
from services.triggers import detect_trigger_score


DEFAULT_WATCHLIST = [
    "BTCUSDC",
    "ETHUSDC",
    "SOLUSDC",
    "XRPUSDC",
    "DOGEUSDC",
    "ADAUSDC",
    "MORPHOUSDC",
]
WATCHLIST = DEFAULT_WATCHLIST.copy()
TIMEFRAME_WEIGHTS = {
    "15m": 0.20,
    "1h": 0.40,
    "4h": 0.30,
    "1d": 0.10,
}
SCAN_CACHE_TTL_SECONDS = 15 * 60
last_scan_results: list[dict[str, Any]] | None = None
last_scan_timestamp: datetime | None = None
scan_in_progress = False


def reset_scan_cache(reset_watchlist: bool = False) -> dict[str, Any]:
    global last_scan_results, last_scan_timestamp, scan_in_progress

    last_scan_results = None
    last_scan_timestamp = None
    scan_in_progress = False

    if reset_watchlist:
        WATCHLIST.clear()
    else:
        WATCHLIST[:] = DEFAULT_WATCHLIST.copy()

    return {
        "last_scan_results": None,
        "last_scan_timestamp": None,
        "scan_in_progress": scan_in_progress,
        "watchlist": WATCHLIST.copy(),
        "reset_watchlist": reset_watchlist,
    }


def analyze_symbol(symbol: str, interval: str = "1h", limit: int = 120) -> dict[str, Any]:
    df = get_klines(symbol=symbol, interval=interval, limit=limit)
    df = add_indicators(df)
    analysis = analyze_market(df)
    analysis["triggers"] = detect_trigger_score(df, analysis)
    analysis["advanced_triggers"] = detect_advanced_triggers(df, analysis)

    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "analysis": analysis,
    }


def _summarize_timeframes(timeframes: dict[str, dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    score_1h = timeframes.get("1h", {}).get("score")
    score_4h = timeframes.get("4h", {}).get("score")

    if score_4h is not None and score_1h is not None:
        if score_4h >= 60 and score_1h < 45:
            reasons.append("Tendance 4h positive mais 1h fragile")
        elif score_4h >= 60:
            reasons.append("Tendance 4h constructive")
        elif score_1h < 45:
            reasons.append("Timeframe 1h fragile")

    if any(
        "Volume faible" in reason
        for data in timeframes.values()
        for reason in data.get("reasons", [])
    ):
        reasons.append("Volume insuffisant pour confirmer")

    trend_1h = timeframes.get("1h", {}).get("trend_strength", {}).get("trend")
    trend_4h = timeframes.get("4h", {}).get("trend_strength", {}).get("trend")
    if trend_1h in {"BEARISH", "VERY_BEARISH"} and trend_4h in {"BEARISH", "VERY_BEARISH"}:
        reasons.append("Marché globalement bearish malgré les niveaux locaux")

    if any(
        data.get("patterns", {}).get("upper_wick", {}).get("detected")
        for data in timeframes.values()
    ):
        reasons.append("Présence de rejet en mèche haute")

    if any(
        data.get("patterns", {}).get("lower_wick", {}).get("detected")
        for data in timeframes.values()
    ):
        reasons.append("Présence de réaction acheteuse en mèche basse")

    if not reasons:
        reasons.append("Aucun signal directionnel fort actuellement")

    return reasons


def _global_trend_strength(timeframes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    weighted_score = 0.0
    available_weight = 0.0
    details: dict[str, Any] = {}

    for interval, weight in TIMEFRAME_WEIGHTS.items():
        trend_strength = timeframes.get(interval, {}).get("trend_strength")
        if not trend_strength:
            continue
        weighted_score += trend_strength.get("score", 0) * weight
        available_weight += weight
        details[interval] = {
            "trend": trend_strength.get("trend"),
            "score": trend_strength.get("score"),
        }

    score = int(round(weighted_score / available_weight)) if available_weight else 0
    return {
        "trend": trend_from_score(score),
        "score": score,
        "details": details,
    }


def analyze_symbol_multi(symbol: str, limit: int = 120) -> dict[str, Any]:
    timeframes: dict[str, dict[str, Any]] = {}
    weighted_score = 0.0
    available_weight = 0.0
    current_price: float | None = None

    for interval, weight in TIMEFRAME_WEIGHTS.items():
        analysis_result = analyze_symbol(symbol=symbol, interval=interval, limit=limit)
        analysis = analysis_result["analysis"]
        timeframes[interval] = analysis

        weighted_score += analysis["score"] * weight
        available_weight += weight

        if interval == "1h":
            current_price = analysis["price"]
        elif current_price is None:
            current_price = analysis["price"]

    if available_weight <= 0:
        raise RuntimeError(f"No timeframe analysis available for {symbol.upper()}")

    global_score = int(round(weighted_score / available_weight))
    reference = timeframes.get("1h") or next(iter(timeframes.values()))
    global_trend_strength = _global_trend_strength(timeframes)

    return {
        "symbol": symbol.upper(),
        "global_score": global_score,
        "global_signal": get_signal(global_score),
        "global_trend": global_trend_strength["trend"],
        "trend_strength": global_trend_strength,
        "current_price": current_price,
        "setup_quality": reference.get("setup_quality"),
        "support": reference.get("support"),
        "resistance": reference.get("resistance"),
        "distance_to_support_pct": reference.get("distance_to_support_pct"),
        "distance_to_resistance_pct": reference.get("distance_to_resistance_pct"),
        "risk_reward": reference.get("risk_reward"),
        "momentum": reference.get("momentum"),
        "patterns": reference.get("patterns"),
        "triggers": reference.get("triggers"),
        "timeframes": timeframes,
        "summary_reasons": _summarize_timeframes(timeframes),
    }


def scan_watchlist(
    interval: str = "1h",
    watchlist: list[str] | None = None,
    limit: int = 120,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for symbol in watchlist or WATCHLIST:
        try:
            analysis_result = analyze_symbol(symbol=symbol, interval=interval, limit=limit)
            analysis = analysis_result["analysis"]
            results.append(
                {
                    "symbol": symbol.upper(),
                    "score": analysis["score"],
                    "signal": analysis["signal"],
                    "price": analysis["price"],
                    "reasons": analysis["reasons"],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "symbol": symbol.upper(),
                    "error": str(exc),
                }
            )

    results.sort(key=lambda item: item.get("score", 0), reverse=True)
    return results


def scan_watchlist_multi(
    watchlist: list[str] | None = None,
    limit: int = 120,
    save_history: bool = True,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for symbol in watchlist or WATCHLIST:
        try:
            result = analyze_symbol_multi(symbol=symbol, limit=limit)
            results.append(result)
            if save_history:
                save_scan_result(result)
        except Exception as exc:
            results.append(
                {
                    "symbol": symbol.upper(),
                    "error": str(exc),
                }
            )

    results.sort(key=lambda item: item.get("global_score", 0), reverse=True)
    return results


def get_cached_scan(
    watchlist: list[str] | None = None,
    limit: int = 120,
    force: bool = False,
    ttl_seconds: int = SCAN_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    global last_scan_results, last_scan_timestamp, scan_in_progress

    now = datetime.now(UTC)
    cache_is_fresh = (
        last_scan_results is not None
        and last_scan_timestamp is not None
        and (now - last_scan_timestamp).total_seconds() < ttl_seconds
    )

    if force or not cache_is_fresh:
        scan_in_progress = True
        try:
            last_scan_results = scan_watchlist_multi(
                watchlist=watchlist,
                limit=limit,
                save_history=True,
            )
            last_scan_timestamp = now
        finally:
            scan_in_progress = False

    next_refresh_at = (
        datetime.fromtimestamp(last_scan_timestamp.timestamp() + ttl_seconds, UTC)
        if last_scan_timestamp
        else None
    )

    return {
        "results": enrich_scan_with_decisions(last_scan_results or []),
        "last_scan_timestamp": last_scan_timestamp.isoformat() if last_scan_timestamp else None,
        "next_refresh_at": next_refresh_at.isoformat() if next_refresh_at else None,
        "cache_ttl_seconds": ttl_seconds,
    }


def enrich_scan_with_decisions(
    results: list[dict[str, Any]],
    positions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for item in results:
        if "error" in item:
            enriched.append(item)
            continue
        decision = build_decision(
            symbol=item["symbol"],
            analysis=item,
            scan_results=results,
            positions=positions,
        )
        enriched.append({**item, "decision_engine": decision})
    return enriched


def get_top_momentum(
    watchlist: list[str] | None = None,
    limit: int = 120,
    count: int = 5,
) -> list[dict[str, Any]]:
    results = scan_watchlist_multi(
        watchlist=watchlist,
        limit=limit,
        save_history=False,
    )
    valid_results = [item for item in results if "error" not in item]

    valid_results.sort(
        key=lambda item: item.get("momentum", {})
        .get("medium", {})
        .get("change_pct", -999),
        reverse=True,
    )
    return valid_results[:count]


def get_top_setups(
    watchlist: list[str] | None = None,
    limit: int = 120,
    count: int = 5,
) -> list[dict[str, Any]]:
    results = scan_watchlist_multi(
        watchlist=watchlist,
        limit=limit,
        save_history=False,
    )
    valid_results = [item for item in results if "error" not in item]

    valid_results.sort(
        key=lambda item: item.get("risk_reward", {}).get("ratio", 0),
        reverse=True,
    )
    return valid_results[:count]
