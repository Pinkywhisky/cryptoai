import pandas as pd
import requests


BASE_URL = "https://api.binance.com"
KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]
NUMERIC_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_asset_volume",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
]


def get_klines(symbol: str, interval: str = "1h", limit: int = 100) -> pd.DataFrame:
    url = f"{BASE_URL}/api/v3/klines"
    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": limit,
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
    except requests.HTTPError as exc:
        message = response.text if "response" in locals() else str(exc)
        raise RuntimeError(f"Binance HTTP error for {symbol.upper()}: {message}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Binance request failed for {symbol.upper()}: {exc}") from exc

    data = response.json()
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected Binance response for {symbol.upper()}: {data}")

    df = pd.DataFrame(data, columns=KLINE_COLUMNS)

    for column in NUMERIC_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["number_of_trades"] = pd.to_numeric(
        df["number_of_trades"], errors="coerce"
    ).fillna(0).astype(int)

    return df
