from typing import Any

import pandas as pd


def _momentum_from_window(df: pd.DataFrame, periods: int) -> dict[str, Any]:
    if df.empty or "close" not in df.columns or periods <= 0:
        return {
            "change_pct": 0.0,
            "direction": "UNKNOWN",
            "strength": "UNKNOWN",
        }

    closes = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(closes) < 2:
        return {
            "change_pct": 0.0,
            "direction": "UNKNOWN",
            "strength": "UNKNOWN",
        }

    window = min(periods, len(closes) - 1)
    start_price = float(closes.iloc[-window - 1])
    end_price = float(closes.iloc[-1])

    if start_price <= 0:
        return {
            "change_pct": 0.0,
            "direction": "UNKNOWN",
            "strength": "UNKNOWN",
        }

    change_pct = (end_price - start_price) / start_price * 100

    if change_pct > 5:
        direction = "BULLISH"
        strength = "STRONG"
    elif change_pct > 2:
        direction = "BULLISH"
        strength = "MEDIUM"
    elif change_pct > 0:
        direction = "BULLISH"
        strength = "WEAK"
    else:
        direction = "BEARISH"
        strength = "BEARISH"

    return {
        "change_pct": round(change_pct, 4),
        "direction": direction,
        "strength": strength,
    }


def calculate_short_momentum(df: pd.DataFrame) -> dict[str, Any]:
    return _momentum_from_window(df, periods=5)


def calculate_medium_momentum(df: pd.DataFrame) -> dict[str, Any]:
    return _momentum_from_window(df, periods=20)


def detect_volume_acceleration(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or "volume" not in df.columns:
        return {
            "ratio": 0.0,
            "detected": False,
            "label": "Volume unavailable",
        }

    volumes = pd.to_numeric(df["volume"].tail(20), errors="coerce").dropna()
    if volumes.empty:
        return {
            "ratio": 0.0,
            "detected": False,
            "label": "Volume unavailable",
        }

    average_volume = float(volumes.mean())
    last_volume = float(volumes.iloc[-1])
    ratio = last_volume / average_volume if average_volume > 0 else 0.0

    if ratio > 2:
        label = "Strong volume acceleration"
    elif ratio > 1.5:
        label = "Volume acceleration"
    else:
        label = "No volume acceleration"

    return {
        "ratio": round(ratio, 4),
        "detected": bool(ratio > 1.5),
        "label": label,
    }
