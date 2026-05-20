from typing import Any

import pandas as pd

from services.momentum import calculate_medium_momentum


def _series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").dropna()


def _last(series: pd.Series) -> float | None:
    if series.empty:
        return None
    return float(series.iloc[-1])


def _slope(series: pd.Series, periods: int = 5) -> float:
    if len(series) < 2:
        return 0.0
    window = min(periods, len(series) - 1)
    previous = float(series.iloc[-window - 1])
    current = float(series.iloc[-1])
    if previous == 0:
        return 0.0
    return (current - previous) / abs(previous) * 100


def _trend_from_score(score: int) -> str:
    if score <= -60:
        return "VERY_BEARISH"
    if score <= -25:
        return "BEARISH"
    if score >= 60:
        return "VERY_BULLISH"
    if score >= 25:
        return "BULLISH"
    return "NEUTRAL"


def trend_from_score(score: int | float) -> str:
    return _trend_from_score(int(round(score)))


def calculate_trend_strength(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "trend": "NEUTRAL",
            "score": 0,
            "details": {
                "ma_alignment": "UNKNOWN",
                "price_vs_ma": "UNKNOWN",
                "ma_slopes": "UNKNOWN",
            },
        }

    close = _series(df, "close")
    ma7 = _series(df, "ma7")
    ma25 = _series(df, "ma25")
    ma99 = _series(df, "ma99")

    price = _last(close)
    last_ma7 = _last(ma7)
    last_ma25 = _last(ma25)
    last_ma99 = _last(ma99)
    score = 0

    ma_alignment = "UNKNOWN"
    if last_ma7 is not None and last_ma25 is not None and last_ma99 is not None:
        if last_ma7 > last_ma25 > last_ma99:
            score += 30
            ma_alignment = "VERY_BULLISH"
        elif last_ma7 > last_ma25:
            score += 15
            ma_alignment = "BULLISH"
        elif last_ma7 < last_ma25 < last_ma99:
            score -= 30
            ma_alignment = "VERY_BEARISH"
        elif last_ma25 < last_ma99:
            score -= 15
            ma_alignment = "BEARISH"
        else:
            ma_alignment = "MIXED"

    price_vs_ma = "UNKNOWN"
    if price is not None and last_ma25 is not None and last_ma99 is not None:
        if price > last_ma25 and price > last_ma99:
            score += 20
            price_vs_ma = "STRONG"
        elif price > last_ma25:
            score += 10
            price_vs_ma = "OK"
        elif price < last_ma25 and price < last_ma99:
            score -= 20
            price_vs_ma = "WEAK"
        else:
            score -= 10
            price_vs_ma = "FRAGILE"

    slopes = {
        "ma7": _slope(ma7),
        "ma25": _slope(ma25),
        "ma99": _slope(ma99),
    }
    if all(value > 0 for value in slopes.values()):
        score += 20
        ma_slopes = "UP"
    elif all(value < 0 for value in slopes.values()):
        score -= 20
        ma_slopes = "DOWN"
    elif slopes["ma25"] < 0 and slopes["ma99"] <= 0:
        score -= 10
        ma_slopes = "WEAK_DOWN"
    elif slopes["ma25"] > 0 and slopes["ma99"] >= 0:
        score += 10
        ma_slopes = "WEAK_UP"
    else:
        ma_slopes = "MIXED"

    medium_momentum = calculate_medium_momentum(df)
    if medium_momentum["direction"] == "BEARISH":
        score -= 15
    elif medium_momentum["strength"] == "STRONG":
        score += 15
    elif medium_momentum["strength"] == "MEDIUM":
        score += 10
    elif medium_momentum["direction"] == "BULLISH":
        score += 5

    volume = _series(df, "volume")
    volume_avg = _series(df, "volume_avg_20")
    last_volume = _last(volume)
    last_volume_avg = _last(volume_avg)
    if last_volume is not None and last_volume_avg is not None and last_volume_avg > 0:
        score += 5 if last_volume >= last_volume_avg else -5

    score = max(-100, min(100, int(round(score))))
    return {
        "trend": _trend_from_score(score),
        "score": score,
        "details": {
            "ma_alignment": ma_alignment,
            "price_vs_ma": price_vs_ma,
            "ma_slopes": ma_slopes,
            "medium_momentum": medium_momentum,
        },
    }
