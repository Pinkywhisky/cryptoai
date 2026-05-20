from typing import Any


def _trend_bucket(item: dict[str, Any]) -> str:
    return item.get("global_trend") or item.get("trend_strength", {}).get("trend") or "NEUTRAL"


def _score_from_btc_trend(trend: str) -> int:
    return {
        "VERY_BULLISH": 70,
        "BULLISH": 60,
        "NEUTRAL": 50,
        "BEARISH": 38,
        "VERY_BEARISH": 25,
    }.get(trend, 50)


def analyze_btc_context(btc_analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    if btc_analysis is None:
        from services.scanner import analyze_symbol_multi

        btc_analysis = analyze_symbol_multi("BTCUSDC", limit=120)

    trend = _trend_bucket(btc_analysis)
    momentum = btc_analysis.get("momentum", {}).get("medium", {})
    score = _score_from_btc_trend(trend)
    if momentum.get("direction") == "BULLISH":
        score += 5
    elif momentum.get("direction") == "BEARISH":
        score -= 5

    return {
        "btc_trend": trend,
        "btc_momentum": momentum.get("direction", "UNKNOWN"),
        "btc_score": max(0, min(100, int(score))),
    }


def analyze_watchlist_context(scan_results: list[dict[str, Any]] | None) -> dict[str, Any]:
    valid_results = [item for item in scan_results or [] if "error" not in item]
    if not valid_results:
        return {
            "watchlist_bullish_pct": 0,
            "watchlist_bearish_pct": 0,
            "watchlist_neutral_pct": 100,
        }

    bullish = sum(1 for item in valid_results if _trend_bucket(item) in {"BULLISH", "VERY_BULLISH"})
    bearish = sum(1 for item in valid_results if _trend_bucket(item) in {"BEARISH", "VERY_BEARISH"})
    total = len(valid_results)
    bullish_pct = round(bullish / total * 100)
    bearish_pct = round(bearish / total * 100)
    return {
        "watchlist_bullish_pct": bullish_pct,
        "watchlist_bearish_pct": bearish_pct,
        "watchlist_neutral_pct": max(0, 100 - bullish_pct - bearish_pct),
    }


def calculate_market_score(
    symbol: str,
    scan_results: list[dict[str, Any]] | None = None,
    btc_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbol = symbol.upper()
    btc_context = btc_context or analyze_btc_context(
        next((item for item in scan_results or [] if item.get("symbol") == "BTCUSDC"), None)
    )
    watchlist_context = analyze_watchlist_context(scan_results)
    score = int(btc_context.get("btc_score", 50))

    if symbol != "BTCUSDC":
        if btc_context.get("btc_trend") == "VERY_BEARISH":
            score -= 20
        elif btc_context.get("btc_trend") == "BEARISH":
            score -= 10
        elif btc_context.get("btc_trend") == "BULLISH":
            score += 5

    if watchlist_context["watchlist_bearish_pct"] > 60:
        score -= 12
    elif watchlist_context["watchlist_bullish_pct"] > 60:
        score += 10

    score = max(0, min(100, score))
    if score < 40:
        message = "Marche globalement defavorable aux entrees agressives."
    elif score >= 60:
        message = "Marche globalement favorable aux setups confirmes."
    else:
        message = "Marche interne neutre, attendre des confirmations propres."

    return {
        **btc_context,
        **watchlist_context,
        "market_score": score,
        "message": message,
    }
