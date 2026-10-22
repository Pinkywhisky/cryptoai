from services import binance_alpha


def test_alpha_token_recognized_and_price_returned(monkeypatch):
    binance_alpha.clear_alpha_cache()

    def fake_request_json(url, params=None):
        if "token/list" in url:
            return {"data": [{"symbol": "CKP", "alphaId": "ALPHA_999"}]}
        if "get-exchange-info" in url:
            return {"data": {"symbols": [{"baseAsset": "ALPHA_999", "quoteAsset": "USDT", "symbol": "ALPHA_999USDT", "status": "TRADING"}]}}
        if "klines" in url:
            assert params["symbol"] == "ALPHA_999USDT"
            now_ms = int(binance_alpha._clock()*1000)
            return {
                "data": [
                    [str(now_ms-(121-i)*3600000), "1", "3", "0.9", str(1 + i / 100), "100", str(now_ms-(120-i)*3600000), "1000", "5", "50", "500", 0]
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
    binance_alpha.clear_alpha_cache()

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
