from services import binance_alpha


def test_alpha_token_recognized_and_price_returned(monkeypatch):
    monkeypatch.setattr(binance_alpha, "_tokens_cache", None)
    monkeypatch.setattr(binance_alpha, "_ticker_cache", {})
    monkeypatch.setattr(binance_alpha, "_candles_cache", {})

    def fake_request_json(url, params=None):
        if "token/list" in url:
            return {"data": [{"symbol": "CKP", "alphaId": "ALPHA_999"}]}
        if "klines" in url:
            assert params["symbol"] == "ALPHA_999USDT"
            return {
                "data": [
                    [str(i), "1", "1.2", "0.9", str(1 + i / 100), "100", str(i + 1), "1000", "5", "50", "500", 0]
                    for i in range(1, 121)
                ]
            }
        assert params["symbol"] == "ALPHA_999USDT"
        return {"data": {"lastPrice": "0.25"}}

    monkeypatch.setattr(binance_alpha, "_request_json", fake_request_json)

    payload = binance_alpha.get_alpha_market_data("CKP")

    assert payload["valid"] is True
    assert payload["market_source"] == "BINANCE_ALPHA"
    assert payload["current_price"] == 0.25
    assert payload["alpha_trading_symbol"] == "ALPHA_999USDT"
    assert payload["candles_available"] is True
    assert payload["global_trend"] in {"BULLISH", "BEARISH", "NEUTRAL"}


def test_alpha_symbol_strips_usdc_and_keeps_native_symbol(monkeypatch):
    monkeypatch.setattr(binance_alpha, "_tokens_cache", None)
    monkeypatch.setattr(binance_alpha, "_ticker_cache", {})
    monkeypatch.setattr(binance_alpha, "_candles_cache", {})

    def fake_request_json(url, params=None):
        if "token/list" in url:
            return {"data": [{"symbol": "MORPHO", "alphaId": "ALPHA_123"}]}
        if "klines" in url:
            return {"data": []}
        return {"data": {"lastPrice": "1.01", "priceChangePercent": "-4.5", "quoteVolume": "433000"}}

    monkeypatch.setattr(binance_alpha, "_request_json", fake_request_json)

    payload = binance_alpha.get_alpha_market_data("MORPHOUSDC")

    assert payload["symbol"] == "MORPHO"
    assert payload["market_source"] == "BINANCE_ALPHA"
    assert payload["current_price"] == 1.01
    assert payload["quote_volume"] == 433000
