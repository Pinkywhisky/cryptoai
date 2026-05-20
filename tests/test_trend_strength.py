import pandas as pd

from services.trend_strength import calculate_trend_strength


def _trend_df(direction: str) -> pd.DataFrame:
    if direction == "bearish":
        close = list(range(130, 10, -1))
        ma7 = [value + 1 for value in close]
        ma25 = [value + 8 for value in close]
        ma99 = [value + 16 for value in close]
    else:
        close = list(range(10, 130))
        ma7 = [value - 1 for value in close]
        ma25 = [value - 8 for value in close]
        ma99 = [value - 16 for value in close]

    return pd.DataFrame(
        {
            "close": close,
            "ma7": ma7,
            "ma25": ma25,
            "ma99": ma99,
            "volume": [120.0 for _ in close],
            "volume_avg_20": [100.0 for _ in close],
        }
    )


def test_trend_strength_bearish() -> None:
    result = calculate_trend_strength(_trend_df("bearish"))

    assert result["trend"] in {"BEARISH", "VERY_BEARISH"}
    assert result["score"] < 0
    assert result["details"]["ma_alignment"] in {"BEARISH", "VERY_BEARISH"}
    assert result["details"]["ma_slopes"] == "DOWN"


def test_trend_strength_bullish() -> None:
    result = calculate_trend_strength(_trend_df("bullish"))

    assert result["trend"] in {"BULLISH", "VERY_BULLISH"}
    assert result["score"] > 0
    assert result["details"]["ma_alignment"] in {"BULLISH", "VERY_BULLISH"}
    assert result["details"]["ma_slopes"] == "UP"
