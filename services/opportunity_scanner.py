from __future__ import annotations

from typing import Any

import requests


BINANCE_TICKER_24H_URL = "https://api.binance.com/api/v3/ticker/24hr"
STABLE_BASES = {
    "USDC",
    "USDT",
    "BUSD",
    "FDUSD",
    "TUSD",
    "DAI",
    "USDP",
    "EUR",
    "EURI",
    "AEUR",
}
EXCLUDED_SUFFIXES = ("UPUSDC", "DOWNUSDC", "BULLUSDC", "BEARUSDC")
EXOTIC_MARKERS = ("3L", "3S", "5L", "5S")
ALLOWED_OPPORTUNITY_DECISIONS = {"BUY_READY", "BUY_WATCH", "WAIT"}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _base_asset(symbol: str, quote: str = "USDC") -> str:
    return symbol[:-len(quote)] if symbol.endswith(quote) else symbol


def is_eligible_symbol(symbol: str, quote: str = "USDC") -> bool:
    symbol = symbol.upper()
    base = _base_asset(symbol, quote)
    if not symbol.endswith(quote):
        return False
    if base in STABLE_BASES:
        return False
    if symbol.endswith(EXCLUDED_SUFFIXES):
        return False
    if any(marker in base for marker in EXOTIC_MARKERS):
        return False
    return True


def _fetch_tickers() -> list[dict[str, Any]]:
    response = requests.get(BINANCE_TICKER_24H_URL, timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected Binance ticker response")
    return payload


def score_market_candidate(ticker: dict[str, Any]) -> dict[str, Any]:
    symbol = str(ticker.get("symbol", "")).upper()
    quote_volume = _to_float(ticker.get("quoteVolume"))
    price_change_pct = _to_float(ticker.get("priceChangePercent"))
    high = _to_float(ticker.get("highPrice"))
    low = _to_float(ticker.get("lowPrice"))
    last_price = _to_float(ticker.get("lastPrice"))
    volume = _to_float(ticker.get("volume"))
    weighted_avg = _to_float(ticker.get("weightedAvgPrice"))

    volatility_pct = ((high - low) / last_price * 100) if last_price > 0 else 0.0
    breakout_potential = last_price > 0 and high > 0 and (high - last_price) / last_price * 100 <= 1.2
    relative_volume = volume / max(weighted_avg, 1.0)

    score = 0.0
    score += min(25, quote_volume / 1_000_000)
    score += min(20, max(0.0, price_change_pct) * 2.0)
    score += min(15, max(0.0, volatility_pct))
    score += 15 if breakout_potential else 0
    score += min(15, relative_volume / 5_000)
    if -8 <= price_change_pct < 0 and breakout_potential:
        score += 8

    reasons: list[str] = []
    if quote_volume >= 5_000_000:
        reasons.append("Volume 24h solide")
    if price_change_pct >= 3:
        reasons.append("Momentum 24h positif")
    if breakout_potential:
        reasons.append("Breakout potentiel proche du plus haut 24h")
    if volatility_pct >= 4:
        reasons.append("Volatilite exploitable")
    if not reasons:
        reasons.append("Candidat liquide mais signal encore discret")

    return {
        "symbol": symbol,
        "scan_score": int(max(0, min(100, round(score)))),
        "reason": ". ".join(reasons),
        "quote_volume": round(quote_volume, 2),
        "price_change_pct": round(price_change_pct, 2),
        "volatility_pct": round(volatility_pct, 2),
        "breakout_potential": bool(breakout_potential),
    }


def get_market_candidates(
    tickers: list[dict[str, Any]] | None = None,
    *,
    max_pairs: int = 300,
    min_quote_volume: float = 1_000_000,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    raw_tickers = tickers if tickers is not None else _fetch_tickers()
    eligible = [
        ticker
        for ticker in raw_tickers
        if is_eligible_symbol(str(ticker.get("symbol", "")))
        and _to_float(ticker.get("quoteVolume")) >= min_quote_volume
    ]
    eligible.sort(key=lambda item: _to_float(item.get("quoteVolume")), reverse=True)

    scored = [score_market_candidate(item) for item in eligible[:max_pairs]]
    scored.sort(key=lambda item: item["scan_score"], reverse=True)
    return scored[:top_n]


def _score_value(decision: dict[str, Any] | None, analysis: dict[str, Any] | None, key: str, default: int = 50) -> int:
    value = (decision or {}).get(key)
    if value is None:
        value = (analysis or {}).get(key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _trend_value(analysis: dict[str, Any] | None) -> str:
    analysis = analysis or {}
    trend = analysis.get("global_trend") or analysis.get("trend_strength", {}).get("trend")
    return str(trend or "NEUTRAL").upper()


def _has_fake_breakout(analysis: dict[str, Any] | None) -> bool:
    advanced = (analysis or {}).get("advanced_triggers") or {}
    signals = advanced.get("signals") if isinstance(advanced, dict) else []
    if isinstance(signals, list):
        return any(
            item.get("detected") and str(item.get("label", "")).lower() == "fake breakout"
            for item in signals
            if isinstance(item, dict)
        )
    return bool(
        isinstance(advanced, dict)
        and advanced.get("detected")
        and str(advanced.get("label", "")).lower() == "fake breakout"
    )


def is_constructive_opportunity(
    candidate: dict[str, Any],
    *,
    analysis: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
) -> bool:
    """True when a scanner candidate deserves attention after full analysis."""
    decision_code = (decision or {}).get("decision", "WAIT")
    if decision_code not in ALLOWED_OPPORTUNITY_DECISIONS:
        return False
    if _trend_value(analysis) == "VERY_BEARISH":
        return False
    if _has_fake_breakout(analysis):
        return False
    if _score_value(decision, analysis, "context_score") < 35:
        return False
    if _score_value(decision, analysis, "setup_score") < 40:
        return False
    if _score_value(decision, analysis, "market_score") < 35:
        return False
    if _score_value(decision, analysis, "risk_score") < 35:
        return False
    if decision and int(decision.get("confidence") or 0) < 20:
        return False
    return int(candidate.get("scan_score") or 0) >= 25


def opportunity_rank(
    candidate: dict[str, Any],
    *,
    decision: dict[str, Any] | None = None,
) -> tuple[int, int, int, str]:
    decision_code = (decision or {}).get("decision")
    decision_rank = {"BUY_READY": 0, "BUY_WATCH": 1, "WAIT": 2}.get(decision_code, 9)
    trigger_score = int((decision or {}).get("trigger_score") or 0)
    scan_score = int(candidate.get("scan_score") or 0)
    return (decision_rank, -trigger_score, -scan_score, str(candidate.get("symbol", "")))


def filter_constructive_opportunities(
    candidates: list[dict[str, Any]],
    *,
    analyses_by_symbol: dict[str, dict[str, Any]] | None = None,
    decisions_by_symbol: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    analyses_by_symbol = analyses_by_symbol or {}
    decisions_by_symbol = decisions_by_symbol or {}
    filtered = [
        candidate
        for candidate in candidates
        if is_constructive_opportunity(
            candidate,
            analysis=analyses_by_symbol.get(str(candidate.get("symbol", "")).upper()),
            decision=decisions_by_symbol.get(str(candidate.get("symbol", "")).upper()),
        )
    ]
    filtered.sort(
        key=lambda candidate: opportunity_rank(
            candidate,
            decision=decisions_by_symbol.get(str(candidate.get("symbol", "")).upper()),
        )
    )
    return filtered


def select_full_analysis_symbols(
    watchlist: list[str],
    candidates: list[dict[str, Any]],
    *,
    limit: int = 30,
) -> list[str]:
    symbols: list[str] = []
    for symbol in watchlist:
        upper = symbol.upper()
        if upper not in symbols:
            symbols.append(upper)
    for candidate in candidates:
        upper = str(candidate.get("symbol", "")).upper()
        if upper and upper not in symbols:
            symbols.append(upper)
        if len(symbols) >= limit:
            break
    return symbols[:limit]
