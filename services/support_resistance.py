from typing import Any

import pandas as pd


def _clean_float(value: Any, decimals: int = 6) -> float | None:
    if pd.isna(value):
        return None

    numeric_value = float(value)
    if pd.isna(numeric_value):
        return None

    return round(numeric_value, decimals)


def detect_support(df: pd.DataFrame, window: int = 20) -> float | None:
    if df.empty or "low" not in df.columns or window <= 0:
        return None

    lows = pd.to_numeric(df["low"].tail(window), errors="coerce").dropna()
    if lows.empty:
        return None

    return _clean_float(lows.min())


def detect_resistance(df: pd.DataFrame, window: int = 20) -> float | None:
    if df.empty or "high" not in df.columns or window <= 0:
        return None

    highs = pd.to_numeric(df["high"].tail(window), errors="coerce").dropna()
    if highs.empty:
        return None

    return _clean_float(highs.max())


def calculate_distances(
    price: float | None,
    support: float | None,
    resistance: float | None,
) -> dict[str, float | None]:
    if price is None or pd.isna(price) or price <= 0:
        return {
            "support": support,
            "resistance": resistance,
            "distance_to_support_pct": None,
            "distance_to_resistance_pct": None,
        }

    distance_to_support = None
    distance_to_resistance = None

    if support is not None and not pd.isna(support):
        distance_to_support = round((price - support) / price * 100, 4)

    if resistance is not None and not pd.isna(resistance):
        distance_to_resistance = round((resistance - price) / price * 100, 4)

    return {
        "support": _clean_float(support),
        "resistance": _clean_float(resistance),
        "distance_to_support_pct": distance_to_support,
        "distance_to_resistance_pct": distance_to_resistance,
    }
