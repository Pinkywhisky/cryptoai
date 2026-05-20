import numpy as np
import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()

    if result.empty:
        for column in ["ma7", "ma25", "ma99", "volume_avg_20", "rsi"]:
            result[column] = pd.Series(dtype="float64")
        return result

    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result["volume"] = pd.to_numeric(result["volume"], errors="coerce")

    result["close"] = result["close"].ffill().bfill().fillna(0.0)
    result["volume"] = result["volume"].fillna(0.0)

    result["ma7"] = result["close"].rolling(window=7, min_periods=1).mean()
    result["ma25"] = result["close"].rolling(window=25, min_periods=1).mean()
    result["ma99"] = result["close"].rolling(window=99, min_periods=1).mean()
    result["volume_avg_20"] = (
        result["volume"].rolling(window=20, min_periods=1).mean()
    )

    delta = result["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(window=14, min_periods=1).mean()
    avg_loss = loss.rolling(window=14, min_periods=1).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((avg_gain > 0) & (avg_loss == 0), 100.0)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss > 0), 0.0)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss == 0), 50.0)
    result["rsi"] = rsi.fillna(50.0).clip(0, 100)

    return result
