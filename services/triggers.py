from typing import Any

import pandas as pd

from services.support_resistance import detect_resistance


def _empty(label: str, reason: str = "Donnees insuffisantes") -> dict[str, Any]:
    return {
        "detected": False,
        "score": 0,
        "label": label,
        "reason": reason,
    }


def _last_number(df: pd.DataFrame, column: str, offset: int = 1) -> float | None:
    if df.empty or column not in df.columns or len(df) < offset:
        return None
    value = pd.to_numeric(pd.Series([df.iloc[-offset][column]]), errors="coerce").iloc[0]
    if pd.isna(value):
        return None
    return float(value)


def _near_level(price: float, level: float, threshold_pct: float = 0.6) -> bool:
    if level <= 0:
        return False
    return abs(price - level) / level * 100 <= threshold_pct


def detect_breakout(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or len(df) < 21:
        return _empty("Breakout non confirme")

    resistance = detect_resistance(df.iloc[:-1], window=20)
    close = _last_number(df, "close")
    volume = _last_number(df, "volume")
    volume_avg = _last_number(df, "volume_avg_20")

    if resistance is None or close is None or volume is None or volume_avg is None:
        return _empty("Breakout non confirme")

    detected = bool(close > resistance and volume > volume_avg * 1.5)
    return {
        "detected": detected,
        "score": 25 if detected else 0,
        "label": "Breakout confirme" if detected else "Breakout non confirme",
        "reason": (
            "Cloture au-dessus de la resistance avec volume superieur a la moyenne."
            if detected
            else "Pas de cloture confirmee au-dessus de la resistance avec volume."
        ),
    }


def detect_ma25_break(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or len(df) < 2 or "ma25" not in df.columns:
        return _empty("Cassure MA25 absente")

    close = _last_number(df, "close")
    previous_close = _last_number(df, "close", offset=2)
    ma25 = _last_number(df, "ma25")
    previous_ma25 = _last_number(df, "ma25", offset=2)
    if None in {close, previous_close, ma25, previous_ma25}:
        return _empty("Cassure MA25 absente")

    detected = bool(close > ma25 and previous_close <= previous_ma25)
    return {
        "detected": detected,
        "score": 18 if detected else 0,
        "label": "Cassure MA25" if detected else "Cassure MA25 absente",
        "reason": (
            "Le prix repasse au-dessus de MA25 apres une cloture precedente sous MA25."
            if detected
            else "Le prix ne valide pas de cassure fraiche de MA25."
        ),
    }


def detect_resistance_break(df: pd.DataFrame, resistance: float | None) -> dict[str, Any]:
    if df.empty or resistance is None:
        return _empty("Cassure resistance absente")

    close = _last_number(df, "close")
    previous_close = _last_number(df, "close", offset=2) if len(df) >= 2 else None
    volume = _last_number(df, "volume")
    volume_avg = _last_number(df, "volume_avg_20")
    if close is None:
        return _empty("Cassure resistance absente")

    volume_confirms = volume is not None and volume_avg is not None and volume > volume_avg
    detected = bool(close > resistance and (previous_close is None or previous_close <= resistance))
    score = 22 if detected and volume_confirms else 14 if detected else 0
    return {
        "detected": detected,
        "score": score,
        "label": "Cassure resistance" if detected else "Cassure resistance absente",
        "reason": (
            "Cloture au-dessus de la resistance avec confirmation volume."
            if detected and volume_confirms
            else "Cloture au-dessus de la resistance, volume a confirmer."
            if detected
            else "La resistance n'est pas cassee en cloture."
        ),
    }


def detect_support_rebound(df: pd.DataFrame, support: float | None) -> dict[str, Any]:
    if df.empty or support is None:
        return _empty("Rebond support absent")

    open_price = _last_number(df, "open")
    low = _last_number(df, "low")
    close = _last_number(df, "close")
    if None in {open_price, low, close}:
        return _empty("Rebond support absent")

    body = abs(close - open_price)
    lower_wick = max(0.0, min(open_price, close) - low)
    detected = bool(_near_level(low, support, threshold_pct=0.8) and close > open_price and lower_wick > body)
    return {
        "detected": detected,
        "score": 20 if detected else 0,
        "label": "Rebond support confirme" if detected else "Rebond support absent",
        "reason": (
            "Le prix teste le support puis cloture vert avec une meche basse significative."
            if detected
            else "Pas de rebond propre confirme sur support."
        ),
    }


def detect_strong_green_close(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or len(df) < 20:
        return _empty("Cloture verte forte absente")

    open_price = _last_number(df, "open")
    close = _last_number(df, "close")
    volume = _last_number(df, "volume")
    volume_avg = _last_number(df, "volume_avg_20")
    if None in {open_price, close, volume, volume_avg}:
        return _empty("Cloture verte forte absente")

    recent = df.tail(20).copy()
    bodies = (pd.to_numeric(recent["close"], errors="coerce") - pd.to_numeric(recent["open"], errors="coerce")).abs()
    average_body = float(bodies.mean()) if not bodies.dropna().empty else 0.0
    body = abs(close - open_price)
    detected = bool(close > open_price and body > average_body and volume > volume_avg)
    return {
        "detected": detected,
        "score": 18 if detected else 0,
        "label": "Cloture verte forte" if detected else "Cloture verte forte absente",
        "reason": (
            "Bougie verte avec corps superieur a la moyenne et volume au-dessus de la moyenne."
            if detected
            else "La derniere cloture ne montre pas assez de force acheteuse."
        ),
    }


def detect_trigger_score(df: pd.DataFrame, analysis: dict[str, Any]) -> dict[str, Any]:
    resistance = analysis.get("resistance")
    support = analysis.get("support")
    checks = [
        detect_breakout(df),
        detect_ma25_break(df),
        detect_resistance_break(df, resistance),
        detect_support_rebound(df, support),
        detect_strong_green_close(df),
    ]
    score = min(100, sum(item["score"] for item in checks))
    detected = [item for item in checks if item["detected"]]

    if score >= 60:
        label = "Trigger confirme"
    elif score >= 30:
        label = "Trigger partiel"
    else:
        label = "Trigger absent"

    return {
        "detected": bool(detected),
        "score": int(score),
        "label": label,
        "reason": detected[0]["reason"] if detected else "Aucun trigger d'entree confirme.",
        "signals": checks,
    }
