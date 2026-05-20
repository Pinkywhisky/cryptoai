import pandas as pd

from services.patterns import (
    detect_buy_pressure,
    detect_consolidation,
    detect_lower_wick,
    detect_sell_pressure,
    detect_upper_wick,
)


def test_detect_buy_pressure_strong() -> None:
    df = pd.DataFrame({"volume": [100.0], "taker_buy_base_volume": [64.0]})

    result = detect_buy_pressure(df)

    assert result["detected"] is True
    assert result["label"] == "Pression acheteuse forte"
    assert result["value"] == 0.64


def test_detect_sell_pressure_strong() -> None:
    df = pd.DataFrame({"volume": [100.0], "taker_buy_base_volume": [35.0]})

    result = detect_sell_pressure(df)

    assert result["detected"] is True
    assert result["label"] == "Pression vendeuse forte"
    assert result["value"] == 0.65


def test_detect_upper_wick() -> None:
    df = pd.DataFrame(
        {
            "open": [100.0],
            "high": [113.0],
            "low": [99.0],
            "close": [104.0],
        }
    )

    result = detect_upper_wick(df)

    assert result["detected"] is True
    assert result["label"] == "Grosse mèche haute"


def test_detect_lower_wick() -> None:
    df = pd.DataFrame(
        {
            "open": [104.0],
            "high": [105.0],
            "low": [91.0],
            "close": [100.0],
        }
    )

    result = detect_lower_wick(df)

    assert result["detected"] is True
    assert result["label"] == "Grosse mèche basse"


def test_detect_consolidation() -> None:
    df = pd.DataFrame(
        {
            "high": [101.0 for _ in range(20)],
            "low": [99.0 for _ in range(20)],
            "close": [100.0 for _ in range(20)],
            "volume": [100.0 for _ in range(20)],
        }
    )

    result = detect_consolidation(df)

    assert result["detected"] is True
    assert result["volume_stable"] is True
