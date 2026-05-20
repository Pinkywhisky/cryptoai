from __future__ import annotations

from typing import Any

import pandas as pd


def _change_pct(data: Any) -> float:
    if isinstance(data, pd.DataFrame):
        if len(data) < 2 or "close" not in data.columns:
            return 0.0
        first = float(pd.to_numeric(data["close"].iloc[0], errors="coerce"))
        last = float(pd.to_numeric(data["close"].iloc[-1], errors="coerce"))
        return (last - first) / first * 100 if first else 0.0
    if isinstance(data, dict):
        momentum = data.get("momentum", {}).get("medium", {})
        if momentum.get("change_pct") is not None:
            return float(momentum["change_pct"])
        if data.get("price_change_pct") is not None:
            return float(data["price_change_pct"])
        if data.get("global_score") is not None:
            return float(data["global_score"]) - 50
    return 0.0


def calculate_relative_strength(symbol_data: Any, btc_data: Any) -> dict[str, Any]:
    symbol_change = _change_pct(symbol_data)
    btc_change = _change_pct(btc_data)
    spread = symbol_change - btc_change
    if spread >= 4:
        label = "STRONG_OUTPERFORM"
        score = 75
    elif spread >= 1:
        label = "OUTPERFORM"
        score = 62
    elif spread <= -4:
        label = "STRONG_UNDERPERFORM"
        score = 25
    elif spread <= -1:
        label = "UNDERPERFORM"
        score = 40
    else:
        label = "INLINE"
        score = 50
    return {
        "relative_strength": label,
        "score": score,
        "symbol_change_pct": round(symbol_change, 2),
        "btc_change_pct": round(btc_change, 2),
        "spread_pct": round(spread, 2),
    }


def btc_altcoin_adjustment(
    symbol: str,
    btc_context: dict[str, Any],
    relative_strength: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if symbol.upper() == "BTCUSDC":
        return {"adjustment": 0, "blocking": False, "reason": "Symbole BTC, pas de penalite altcoin."}

    btc_trend = btc_context.get("btc_trend") or btc_context.get("trend") or "NEUTRAL"
    rs_label = (relative_strength or {}).get("relative_strength")
    adjustment = 0
    blocking = False
    reason = "Contexte BTC neutre."

    if btc_trend == "VERY_BEARISH":
        adjustment = -18
        blocking = rs_label != "STRONG_OUTPERFORM"
        reason = "BTC tres bearish, penalite forte sur altcoin."
    elif btc_trend == "BEARISH":
        adjustment = -10
        reason = "BTC bearish, prudence sur altcoin."
    elif btc_trend in {"BULLISH", "VERY_BULLISH"}:
        adjustment = 6
        reason = "BTC constructif, leger bonus aux altcoins."
    if rs_label in {"OUTPERFORM", "STRONG_OUTPERFORM"}:
        adjustment += 8
        reason = "Altcoin fort relativement a BTC."

    return {"adjustment": adjustment, "blocking": blocking, "reason": reason}
