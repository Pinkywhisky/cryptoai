from typing import Any

import pandas as pd


def _last_value(df: pd.DataFrame, column: str) -> float | None:
    if df.empty or column not in df.columns:
        return None

    value = pd.to_numeric(pd.Series([df.iloc[-1][column]]), errors="coerce").iloc[0]
    if pd.isna(value):
        return None
    return float(value)


def _format_percent(value: float) -> str:
    return f"{round(value * 100)}%"


def _buy_ratio(df: pd.DataFrame) -> float | None:
    volume = _last_value(df, "volume")
    taker_buy_volume = _last_value(df, "taker_buy_base_volume")

    if volume is None or taker_buy_volume is None or volume <= 0:
        return None

    return taker_buy_volume / volume


def detect_buy_pressure(df: pd.DataFrame) -> dict[str, Any]:
    ratio = _buy_ratio(df)
    if ratio is None:
        return {
            "detected": False,
            "label": "Pression acheteuse inconnue",
            "value": None,
            "reason": "Volume acheteur indisponible",
        }

    if ratio > 0.60:
        return {
            "detected": True,
            "label": "Pression acheteuse forte",
            "value": round(ratio, 4),
            "reason": f"{_format_percent(ratio)} du volume est côté acheteur",
        }

    label = "Pression vendeuse forte" if ratio < 0.40 else "Pression neutre"
    return {
        "detected": False,
        "label": label,
        "value": round(ratio, 4),
        "reason": f"{_format_percent(ratio)} du volume est côté acheteur",
    }


def detect_sell_pressure(df: pd.DataFrame) -> dict[str, Any]:
    ratio = _buy_ratio(df)
    if ratio is None:
        return {
            "detected": False,
            "label": "Pression vendeuse inconnue",
            "value": None,
            "reason": "Volume acheteur indisponible",
        }

    if ratio < 0.40:
        return {
            "detected": True,
            "label": "Pression vendeuse forte",
            "value": round(1 - ratio, 4),
            "reason": f"{_format_percent(1 - ratio)} du volume est côté vendeur",
        }

    label = "Pression acheteuse forte" if ratio > 0.60 else "Pression neutre"
    return {
        "detected": False,
        "label": label,
        "value": round(1 - ratio, 4),
        "reason": f"{_format_percent(1 - ratio)} du volume est côté vendeur",
    }


def detect_upper_wick(df: pd.DataFrame) -> dict[str, Any]:
    open_price = _last_value(df, "open")
    high = _last_value(df, "high")
    close = _last_value(df, "close")

    if open_price is None or high is None or close is None:
        return {
            "detected": False,
            "label": "Mèche haute inconnue",
            "value": None,
            "reason": "Données OHLC indisponibles",
        }

    body = abs(close - open_price)
    upper_wick = max(0.0, high - max(open_price, close))
    detected = bool(upper_wick > body * 2)

    return {
        "detected": detected,
        "label": "Grosse mèche haute" if detected else "Pas de grosse mèche haute",
        "value": round(upper_wick, 8),
        "reason": "Pression vendeuse visible en haut de bougie"
        if detected
        else "Pas de rejet haut significatif",
    }


def detect_lower_wick(df: pd.DataFrame) -> dict[str, Any]:
    open_price = _last_value(df, "open")
    low = _last_value(df, "low")
    close = _last_value(df, "close")

    if open_price is None or low is None or close is None:
        return {
            "detected": False,
            "label": "Mèche basse inconnue",
            "value": None,
            "reason": "Données OHLC indisponibles",
        }

    body = abs(close - open_price)
    lower_wick = max(0.0, min(open_price, close) - low)
    detected = bool(lower_wick > body * 2)

    return {
        "detected": detected,
        "label": "Grosse mèche basse" if detected else "Pas de grosse mèche basse",
        "value": round(lower_wick, 8),
        "reason": "Rebond acheteur visible en bas de bougie"
        if detected
        else "Pas de rejet bas significatif",
    }


def detect_consolidation(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or not {"high", "low", "close", "volume"}.issubset(df.columns):
        return {
            "detected": False,
            "label": "Consolidation inconnue",
            "value": None,
            "volume_stable": False,
            "reason": "Données insuffisantes pour mesurer la consolidation",
        }

    recent = df.tail(20).copy()
    for column in ["high", "low", "close", "volume"]:
        recent[column] = pd.to_numeric(recent[column], errors="coerce")

    last_close = recent["close"].iloc[-1]
    if pd.isna(last_close) or last_close <= 0:
        return {
            "detected": False,
            "label": "Consolidation inconnue",
            "value": None,
            "volume_stable": False,
            "reason": "Dernier prix indisponible",
        }

    range_pct = (recent["high"].max() - recent["low"].min()) / last_close * 100
    volume_avg = recent["volume"].mean()
    last_volume = recent["volume"].iloc[-1]
    volume_ratio = last_volume / volume_avg if volume_avg and volume_avg > 0 else None
    volume_stable = bool(volume_ratio is not None and 0.8 <= volume_ratio <= 1.2)
    detected = bool(range_pct < 2.5)

    return {
        "detected": detected,
        "label": "Consolidation probable" if detected else "Pas de consolidation",
        "value": round(float(range_pct), 4),
        "volume_stable": volume_stable,
        "volume_ratio": round(float(volume_ratio), 4) if volume_ratio else None,
        "reason": f"Range 20 bougies de {round(float(range_pct), 2)}%",
    }
