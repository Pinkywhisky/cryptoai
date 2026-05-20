import pandas as pd


def _quality_from_ratio(ratio: float) -> str:
    if ratio >= 3:
        return "EXCELLENT"
    if ratio >= 2:
        return "BON"
    if ratio >= 1:
        return "MOYEN"
    return "MAUVAIS"


def calculate_risk_reward(
    price: float | None,
    support: float | None,
    resistance: float | None,
) -> dict[str, float | str]:
    invalid = (
        price is None
        or support is None
        or resistance is None
        or pd.isna(price)
        or pd.isna(support)
        or pd.isna(resistance)
        or price <= 0
        or support >= price
        or resistance <= price
    )

    if invalid:
        return {
            "risk_pct": 0.0,
            "reward_pct": 0.0,
            "ratio": 0.0,
            "quality": "INVALID",
        }

    risk_pct = (price - support) / price * 100
    reward_pct = (resistance - price) / price * 100
    ratio = reward_pct / risk_pct if risk_pct > 0 else 0.0

    return {
        "risk_pct": round(risk_pct, 4),
        "reward_pct": round(reward_pct, 4),
        "ratio": round(ratio, 4),
        "quality": _quality_from_ratio(ratio),
    }
