import pandas as pd

from services.momentum import (
    calculate_medium_momentum,
    calculate_short_momentum,
    detect_volume_acceleration,
)


def test_calculate_short_momentum_strong() -> None:
    df = pd.DataFrame({"close": [100.0, 101.0, 102.0, 103.0, 104.0, 108.0]})

    result = calculate_short_momentum(df)

    assert result["direction"] == "BULLISH"
    assert result["strength"] == "STRONG"


def test_calculate_medium_momentum_bearish() -> None:
    df = pd.DataFrame({"close": [120.0 - index for index in range(25)]})

    result = calculate_medium_momentum(df)

    assert result["direction"] == "BEARISH"
    assert result["strength"] == "BEARISH"


def test_detect_volume_acceleration() -> None:
    df = pd.DataFrame({"volume": [100.0 for _ in range(19)] + [220.0]})

    result = detect_volume_acceleration(df)

    assert result["detected"] is True
    assert result["label"] == "Strong volume acceleration"
