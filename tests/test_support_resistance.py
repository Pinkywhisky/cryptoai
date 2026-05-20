import pandas as pd

from services.support_resistance import (
    calculate_distances,
    detect_resistance,
    detect_support,
)


def test_detect_support_uses_lowest_recent_low() -> None:
    df = pd.DataFrame({"low": [100.0, 98.0, 95.0, 97.0, 99.0]})

    assert detect_support(df, window=3) == 95.0


def test_detect_resistance_uses_highest_recent_high() -> None:
    df = pd.DataFrame({"high": [100.0, 105.0, 103.0, 108.0, 101.0]})

    assert detect_resistance(df, window=4) == 108.0


def test_calculate_distances_handles_valid_levels() -> None:
    result = calculate_distances(price=100.0, support=98.0, resistance=105.0)

    assert result["distance_to_support_pct"] == 2.0
    assert result["distance_to_resistance_pct"] == 5.0


def test_calculate_distances_handles_invalid_price() -> None:
    result = calculate_distances(price=0.0, support=98.0, resistance=105.0)

    assert result["distance_to_support_pct"] is None
    assert result["distance_to_resistance_pct"] is None
