import pandas as pd

import services.scanner as scanner


def _df_for_symbol(symbol: str, interval: str) -> pd.DataFrame:
    offset = {"15m": 0, "1h": 5, "4h": 10, "1d": 15}[interval]
    return pd.DataFrame(
        {
            "open": [100.0 + offset + index for index in range(120)],
            "high": [101.0 + offset + index for index in range(120)],
            "low": [99.0 + offset + index for index in range(120)],
            "close": [100.0 + offset + index for index in range(120)],
            "volume": [100.0 + index for index in range(120)],
            "taker_buy_base_volume": [70.0 + index * 0.6 for index in range(120)],
        }
    )


def test_analyze_symbol_multi_uses_weighted_timeframes(monkeypatch) -> None:
    def fake_get_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
        return _df_for_symbol(symbol, interval)

    monkeypatch.setattr(scanner, "get_klines", fake_get_klines)

    result = scanner.analyze_symbol_multi("BTCUSDC")

    assert result["symbol"] == "BTCUSDC"
    assert set(result["timeframes"]) == {"15m", "1h", "4h", "1d"}
    assert "global_score" in result
    assert result["global_signal"] in {
        "ACHAT POTENTIEL",
        "ATTENDRE",
        "ÉVITER / VENTE POSSIBLE",
    }


def test_scan_watchlist_multi_keeps_symbol_errors(monkeypatch) -> None:
    def fake_get_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
        if symbol == "MORPHOUSDC":
            raise RuntimeError("symbol not found")
        return _df_for_symbol(symbol, interval)

    monkeypatch.setattr(scanner, "get_klines", fake_get_klines)

    result = scanner.scan_watchlist_multi(["BTCUSDC", "MORPHOUSDC"])

    assert result[0]["symbol"] == "BTCUSDC"
    assert result[1]["symbol"] == "MORPHOUSDC"
    assert "error" in result[1]
