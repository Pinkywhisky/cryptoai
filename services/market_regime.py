from __future__ import annotations

from typing import Any


def _trend(item: dict[str, Any]) -> str:
    return item.get("global_trend") or item.get("trend_strength", {}).get("trend") or "NEUTRAL"


def determine_market_regime(
    scan_results: list[dict[str, Any]] | None,
    btc_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    valid = [item for item in scan_results or [] if "error" not in item]
    if not valid:
        return {
            "regime": "NEUTRAL",
            "confidence": 35,
            "message": "Donnees de marche insuffisantes.",
            "bullish_pct": 0,
            "bearish_pct": 0,
        }

    bullish = sum(1 for item in valid if _trend(item) in {"BULLISH", "VERY_BULLISH"})
    bearish = sum(1 for item in valid if _trend(item) in {"BEARISH", "VERY_BEARISH"})
    avg_score = sum(float(item.get("global_score", item.get("score", 50))) for item in valid) / len(valid)
    bullish_pct = round(bullish / len(valid) * 100)
    bearish_pct = round(bearish / len(valid) * 100)
    btc_trend = (btc_context or {}).get("btc_trend") or next(
        (_trend(item) for item in valid if item.get("symbol") == "BTCUSDC"),
        "NEUTRAL",
    )

    if btc_trend == "VERY_BEARISH" and bearish_pct >= 55:
        regime = "PANIC"
        confidence = 85
        message = "Marche sous pression forte, priorite a la defense."
    elif bearish_pct >= 60 or (btc_trend in {"BEARISH", "VERY_BEARISH"} and avg_score < 45):
        regime = "RISK_OFF"
        confidence = 74
        message = "Marche globalement defensif."
    elif bullish_pct >= 60 and btc_trend in {"BULLISH", "VERY_BULLISH"}:
        regime = "RISK_ON"
        confidence = 76
        message = "Marche favorable aux setups confirmes."
    elif bullish_pct >= 45 and avg_score >= 58:
        regime = "SPECULATIVE"
        confidence = 64
        message = "Marche actif mais selectif, privilegier les triggers propres."
    else:
        regime = "NEUTRAL"
        confidence = 55
        message = "Marche mixte, attendre les confirmations."

    return {
        "regime": regime,
        "confidence": confidence,
        "message": message,
        "bullish_pct": bullish_pct,
        "bearish_pct": bearish_pct,
        "avg_score": round(avg_score, 2),
        "btc_trend": btc_trend,
    }
