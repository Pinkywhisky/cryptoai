from __future__ import annotations

from typing import Any


def _state(item: dict[str, Any]) -> tuple[str, str]:
    decision = item.get("decision_engine", {}).get("decision") or item.get("decision")
    trend = item.get("global_trend") or item.get("trend_strength", {}).get("trend") or "NEUTRAL"
    score = int(item.get("global_score", item.get("score", 50)) or 50)

    if decision in {"BUY_READY", "TAKE_PROFIT"} or trend == "VERY_BULLISH" or score >= 70:
        return "bullish", "Fort"
    if decision == "BUY_WATCH" or trend == "BULLISH" or score >= 58:
        return "weak_bullish", "Constructif"
    if decision in {"CUT_LOSS", "AVOID"} or trend == "VERY_BEARISH" or score < 35:
        return "bearish", "Faible"
    if trend == "BEARISH" or score < 45:
        return "weak_bearish", "Fragile"
    return "neutral", "Neutre"


def generate_market_heatmap(scan_results: list[dict[str, Any]] | None, *, limit: int = 24) -> list[dict[str, Any]]:
    heatmap: list[dict[str, Any]] = []
    for item in scan_results or []:
        if "error" in item:
            continue
        state, label = _state(item)
        heatmap.append(
            {
                "symbol": item.get("symbol"),
                "state": state,
                "label": label,
                "score": item.get("global_score", item.get("score")),
                "decision": item.get("decision_engine", {}).get("decision"),
                "confidence": item.get("decision_engine", {}).get("confidence"),
            }
        )
    heatmap.sort(key=lambda item: item.get("score") or 0, reverse=True)
    return heatmap[:limit]
