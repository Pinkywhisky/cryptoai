import pandas as pd

from services.indicators import add_indicators


def test_add_indicators_calculates_moving_averages() -> None:
    df = pd.DataFrame(
        {
            "close": [float(value) for value in range(1, 101)],
            "volume": [100.0 for _ in range(100)],
        }
    )

    result = add_indicators(df)

    assert result.iloc[-1]["ma7"] == 97.0
    assert result.iloc[-1]["ma25"] == 88.0
    assert result.iloc[-1]["ma99"] == 51.0
    assert result.iloc[-1]["volume_avg_20"] == 100.0


def test_add_indicators_calculates_rsi_for_uptrend() -> None:
    df = pd.DataFrame(
        {
            "close": [float(value) for value in range(1, 31)],
            "volume": [100.0 for _ in range(30)],
        }
    )

    result = add_indicators(df)

    assert result.iloc[-1]["rsi"] == 100.0


def test_add_indicators_handles_short_input_without_nan() -> None:
    df = pd.DataFrame(
        {
            "close": [10.0, 10.0, 10.0],
            "volume": [100.0, 110.0, 120.0],
        }
    )

    result = add_indicators(df)

    assert result[["ma7", "ma25", "ma99", "volume_avg_20", "rsi"]].notna().all().all()
    assert result.iloc[-1]["rsi"] == 50.0
