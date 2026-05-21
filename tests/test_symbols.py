from services import symbols


def test_validate_symbol_converts_base_asset_from_cache(monkeypatch):
    monkeypatch.setattr(symbols, "get_exchange_symbols", lambda force=False: {"BTCUSDC"})
    symbols.clear_symbol_cache()

    result = symbols.validate_symbol("btc")

    assert result == {
        "valid": True,
        "symbol": "BTCUSDC",
        "base_asset": "BTC",
        "quote_asset": "USDC",
        "market_source": "BINANCE_SPOT",
    }


def test_validate_symbol_rejects_unknown_pair(monkeypatch):
    monkeypatch.setattr(symbols, "COMMON_USDC_SYMBOLS", set())
    monkeypatch.setattr(symbols, "get_exchange_symbols", lambda force=False: {"BTCUSDC"})
    symbols.clear_symbol_cache()

    result = symbols.validate_symbol("CKP")

    assert result["valid"] is False
    assert result["error"] == "Invalid symbol"


def test_resolve_market_symbol_detects_spot(monkeypatch):
    monkeypatch.setattr(symbols, "COMMON_USDC_SYMBOLS", {"BTCUSDC"})

    result = symbols.resolve_market_symbol("BTC")

    assert result["valid"] is True
    assert result["symbol"] == "BTCUSDC"
    assert result["market_source"] == "BINANCE_SPOT"


def test_resolve_market_symbol_prefers_exact_spot_before_alpha(monkeypatch):
    monkeypatch.setattr(symbols, "COMMON_USDC_SYMBOLS", set())
    monkeypatch.setattr(symbols, "get_exchange_symbols", lambda force=False: {"MORPHOUSDC"})

    def unexpected_alpha(symbol):
        raise AssertionError("Alpha should not be queried when exact Spot exists")

    monkeypatch.setattr(symbols, "get_alpha_market_data", unexpected_alpha)

    result = symbols.resolve_market_symbol("MORPHOUSDC")

    assert result["valid"] is True
    assert result["symbol"] == "MORPHOUSDC"
    assert result["base_asset"] == "MORPHO"
    assert result["quote_asset"] == "USDC"
    assert result["market_source"] == "BINANCE_SPOT"


def test_resolve_market_symbol_base_fallback_usdc_before_alpha(monkeypatch):
    monkeypatch.setattr(symbols, "COMMON_USDC_SYMBOLS", set())
    monkeypatch.setattr(symbols, "get_exchange_symbols", lambda force=False: {"MORPHOUSDC"})

    def unexpected_alpha(symbol):
        raise AssertionError("Alpha should not be queried when BASEUSDC exists")

    monkeypatch.setattr(symbols, "get_alpha_market_data", unexpected_alpha)

    result = symbols.resolve_market_symbol("MORPHO")

    assert result["symbol"] == "MORPHOUSDC"
    assert result["market_source"] == "BINANCE_SPOT"


def test_resolve_market_symbol_base_fallback_usdt_before_alpha(monkeypatch):
    monkeypatch.setattr(symbols, "COMMON_USDC_SYMBOLS", set())
    monkeypatch.setattr(symbols, "get_exchange_symbols", lambda force=False: {"MORPHOUSDT"})

    def unexpected_alpha(symbol):
        raise AssertionError("Alpha should not be queried when BASEUSDT exists")

    monkeypatch.setattr(symbols, "get_alpha_market_data", unexpected_alpha)

    result = symbols.resolve_market_symbol("MORPHO")

    assert result["symbol"] == "MORPHOUSDT"
    assert result["quote_asset"] == "USDT"
    assert result["market_source"] == "BINANCE_SPOT"


def test_resolve_market_symbol_detects_alpha(monkeypatch):
    monkeypatch.setattr(symbols, "COMMON_USDC_SYMBOLS", set())
    monkeypatch.setattr(symbols, "get_exchange_symbols", lambda force=False: {"BTCUSDC"})
    monkeypatch.setattr(
        symbols,
        "get_alpha_market_data",
        lambda symbol: {"valid": True, "symbol": "CKP", "market_source": "BINANCE_ALPHA"},
    )

    result = symbols.resolve_market_symbol("CKP")

    assert result == {
        "valid": True,
        "symbol": "CKP",
        "resolved_symbol": "CKP",
        "base_asset": "CKP",
        "quote_asset": None,
        "market_source": "BINANCE_ALPHA",
    }


def test_resolve_market_symbol_rejects_unknown(monkeypatch):
    monkeypatch.setattr(symbols, "COMMON_USDC_SYMBOLS", set())
    monkeypatch.setattr(symbols, "get_exchange_symbols", lambda force=False: {"BTCUSDC"})
    monkeypatch.setattr(symbols, "get_alpha_market_data", lambda symbol: {"valid": False})

    result = symbols.resolve_market_symbol("NOPE")

    assert result["valid"] is False
    assert result["market_source"] == "UNKNOWN"
