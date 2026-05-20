import math
from typing import Any

import pandas as pd

from services.momentum import (
    calculate_medium_momentum,
    calculate_short_momentum,
    detect_volume_acceleration,
)
from services.patterns import (
    detect_buy_pressure,
    detect_consolidation,
    detect_lower_wick,
    detect_sell_pressure,
    detect_upper_wick,
)
from services.risk_reward import calculate_risk_reward
from services.support_resistance import (
    calculate_distances,
    detect_resistance,
    detect_support,
)
from services.trend_strength import calculate_trend_strength


BUY_SIGNAL = "ACHAT POTENTIEL"
WAIT_SIGNAL = "ATTENDRE"
SELL_SIGNAL = "ÉVITER / VENTE POSSIBLE"
QUALITY_ORDER = ["INVALID", "MAUVAIS", "MOYEN", "BON", "EXCELLENT"]


def _clean_float(value: Any, decimals: int) -> float | None:
    if pd.isna(value):
        return None
    numeric_value = float(value)
    if math.isnan(numeric_value) or math.isinf(numeric_value):
        return None
    return round(numeric_value, decimals)


def _is_available(value: Any) -> bool:
    return not pd.isna(value)


def get_signal(score: int) -> str:
    if score >= 70:
        return BUY_SIGNAL
    if score >= 45:
        return WAIT_SIGNAL
    return SELL_SIGNAL


def _cap_quality(quality: str, maximum: str) -> str:
    if quality not in QUALITY_ORDER:
        return maximum
    if maximum not in QUALITY_ORDER:
        return quality
    return QUALITY_ORDER[min(QUALITY_ORDER.index(quality), QUALITY_ORDER.index(maximum))]


def _downgrade_quality(quality: str, steps: int = 1) -> str:
    if quality not in QUALITY_ORDER:
        return quality
    index = max(1, QUALITY_ORDER.index(quality) - steps)
    return QUALITY_ORDER[index]


def _contextual_setup_quality(
    risk_reward_quality: str,
    trend_strength: dict[str, Any],
    momentum: dict[str, Any],
    last: pd.Series,
) -> tuple[str, list[str]]:
    quality = risk_reward_quality
    reasons: list[str] = []
    trend = trend_strength.get("trend")
    medium_momentum = momentum.get("medium", {})
    price = last.get("close")
    ma25 = last.get("ma25")
    ma99 = last.get("ma99")
    volume = last.get("volume")
    volume_avg_20 = last.get("volume_avg_20")

    if trend == "VERY_BEARISH":
        capped = _cap_quality(quality, "MAUVAIS")
        if capped != quality:
            reasons.append("Setup quality plafonnée : tendance globale très baissière")
        quality = capped
    elif trend == "BEARISH":
        capped = _cap_quality(quality, "MOYEN")
        if capped != quality:
            reasons.append("Setup quality plafonnée : tendance globale baissière")
        quality = capped

    if medium_momentum.get("direction") == "BEARISH" and medium_momentum.get("change_pct", 0) <= -2:
        downgraded = _downgrade_quality(quality)
        if downgraded != quality:
            reasons.append("Setup quality réduite : momentum moyen baissier")
        quality = downgraded

    if (
        _is_available(price)
        and _is_available(ma25)
        and _is_available(ma99)
        and price < ma25
        and price < ma99
    ):
        capped = _cap_quality(quality, "BON")
        if capped != quality:
            reasons.append("Setup quality plafonnée : prix sous MA25 et MA99")
        quality = capped

    if (
        trend in {"BEARISH", "VERY_BEARISH"}
        and _is_available(volume)
        and _is_available(volume_avg_20)
        and volume < volume_avg_20
    ):
        capped = _cap_quality(quality, "MOYEN")
        if capped != quality:
            reasons.append("Setup quality plafonnée : volume faible dans tendance baissière")
        quality = capped

    return quality, reasons


def analyze_market(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        raise ValueError("Cannot analyze an empty DataFrame")

    last = df.iloc[-1]
    price = last["close"]
    patterns = {
        "buy_pressure": detect_buy_pressure(df),
        "sell_pressure": detect_sell_pressure(df),
        "upper_wick": detect_upper_wick(df),
        "lower_wick": detect_lower_wick(df),
        "consolidation": detect_consolidation(df),
    }
    support = detect_support(df)
    resistance = detect_resistance(df)
    distances = calculate_distances(float(price), support, resistance)
    risk_reward = calculate_risk_reward(float(price), support, resistance)
    momentum = {
        "short": calculate_short_momentum(df),
        "medium": calculate_medium_momentum(df),
        "volume_acceleration": detect_volume_acceleration(df),
    }
    trend_strength = calculate_trend_strength(df)
    setup_quality, setup_quality_reasons = _contextual_setup_quality(
        risk_reward["quality"],
        trend_strength,
        momentum,
        last,
    )

    score = 50
    reasons: list[str] = []

    if _is_available(last["ma7"]) and _is_available(last["ma25"]):
        if last["ma7"] > last["ma25"]:
            score += 12
            reasons.append("MA7 au-dessus de MA25 : tendance courte positive")
        else:
            score -= 12
            reasons.append("MA7 sous MA25 : tendance courte fragile")
    else:
        reasons.append("MA7/MA25 indisponibles : tendance courte non confirmée")

    if _is_available(last["ma25"]) and _is_available(last["ma99"]):
        if last["ma25"] > last["ma99"]:
            score += 12
            reasons.append("MA25 au-dessus de MA99 : tendance de fond positive")
        else:
            score -= 12
            reasons.append("MA25 sous MA99 : tendance de fond faible")
    else:
        reasons.append("MA25/MA99 indisponibles : tendance de fond non confirmée")

    if _is_available(price) and _is_available(last["ma7"]):
        if price > last["ma7"]:
            score += 8
            reasons.append("Prix au-dessus de MA7 : momentum positif")
        else:
            score -= 8
            reasons.append("Prix sous MA7 : faiblesse court terme")
    else:
        reasons.append("Prix/MA7 indisponibles : momentum non confirmé")

    if _is_available(last["volume"]) and _is_available(last["volume_avg_20"]):
        if last["volume"] > last["volume_avg_20"]:
            score += 8
            reasons.append("Volume supérieur à la moyenne : mouvement confirmé")
        else:
            score -= 4
            reasons.append("Volume faible : manque de confirmation")
    else:
        reasons.append("Volume moyen indisponible : confirmation faible")

    if not _is_available(last["rsi"]):
        reasons.append("RSI indisponible")
    elif last["rsi"] < 30:
        score += 8
        reasons.append("RSI bas : possible zone de rebond")
    elif last["rsi"] > 70:
        score -= 10
        reasons.append("RSI élevé : risque de surachat")
    else:
        score += 4
        reasons.append("RSI neutre")

    if patterns["buy_pressure"]["detected"]:
        score += 10
        reasons.append(patterns["buy_pressure"]["label"])

    if patterns["sell_pressure"]["detected"]:
        score -= 10
        reasons.append(patterns["sell_pressure"]["label"])

    if patterns["upper_wick"]["detected"]:
        score -= 15
        reasons.append(patterns["upper_wick"]["label"])

    if patterns["lower_wick"]["detected"]:
        score += 10
        reasons.append(patterns["lower_wick"]["label"])

    if patterns["consolidation"]["detected"]:
        if score >= 45:
            score += 5
            reasons.append("Consolidation probable : base de construction possible")
        else:
            score -= 5
            reasons.append("Consolidation probable après faiblesse : prudence")

    if patterns["consolidation"].get("volume_stable"):
        reasons.append("Volume stable pendant la consolidation")

    if setup_quality == "EXCELLENT":
        score += 20
        reasons.append("Risk/reward excellent confirmé par le contexte")
    elif setup_quality == "BON":
        score += 10
        reasons.append("Risk/reward bon dans le contexte actuel")
    elif setup_quality == "MAUVAIS":
        score -= 10
        reasons.append("Setup quality faible")

    reasons.extend(setup_quality_reasons)

    medium_momentum = momentum["medium"]
    if medium_momentum["strength"] == "STRONG":
        score += 10
        reasons.append("Momentum moyen fort")
    elif medium_momentum["strength"] == "MEDIUM":
        score += 5
        reasons.append("Momentum moyen positif")
    elif medium_momentum["direction"] == "BEARISH":
        score -= 10
        reasons.append("Momentum moyen baissier")

    if momentum["volume_acceleration"]["detected"]:
        score += 10
        reasons.append(momentum["volume_acceleration"]["label"])

    distance_to_support = distances["distance_to_support_pct"]
    distance_to_resistance = distances["distance_to_resistance_pct"]

    if distance_to_support is not None and 0 <= distance_to_support < 2:
        score += 5
        reasons.append("Prix proche du support")

    if distance_to_resistance is not None and 0 <= distance_to_resistance < 1:
        score -= 15
        reasons.append("Prix trop proche de la résistance")

    score = int(max(0, min(100, score)))

    return {
        "signal": get_signal(score),
        "score": score,
        "setup_quality": setup_quality,
        "price": _clean_float(price, 6),
        "rsi": _clean_float(last["rsi"], 2),
        "ma7": _clean_float(last["ma7"], 6),
        "ma25": _clean_float(last["ma25"], 6),
        "ma99": _clean_float(last["ma99"], 6),
        "support": distances["support"],
        "resistance": distances["resistance"],
        "distance_to_support_pct": distances["distance_to_support_pct"],
        "distance_to_resistance_pct": distances["distance_to_resistance_pct"],
        "risk_reward": risk_reward,
        "momentum": momentum,
        "trend_strength": trend_strength,
        "patterns": patterns,
        "reasons": reasons,
    }
