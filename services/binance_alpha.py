from __future__ import annotations

import logging
import time
from typing import Any

import requests


ALPHA_BASE_URL = "https://www.binance.com"
ALPHA_TOKEN_LIST_URL = (
    f"{ALPHA_BASE_URL}/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"
)
ALPHA_TICKER_URL = f"{ALPHA_BASE_URL}/bapi/defi/v1/public/alpha-trade/ticker"
ALPHA_KLINES_URL = f"{ALPHA_BASE_URL}/bapi/defi/v1/public/alpha-trade/klines"
CACHE_TTL_SECONDS = 10 * 60

MARKET_SOURCE_SPOT = "BINANCE_SPOT"
MARKET_SOURCE_ALPHA = "BINANCE_ALPHA"
MARKET_SOURCE_MANUAL = "MANUAL"
MARKET_SOURCE_UNKNOWN = "UNKNOWN"

logger = logging.getLogger(__name__)

_tokens_cache: dict[str, dict[str, Any]] | None = None
_tokens_loaded_at = 0.0
_ticker_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_candles_cache: dict[tuple[str, str, int], tuple[float, list[dict[str, Any]]]] = {}


def _normalize_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper().replace("/", "").replace("-", "")
    for suffix in ("USDC", "USDT"):
        if raw.endswith(suffix) and len(raw) > len(suffix):
            return raw[: -len(suffix)]
    return raw


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _extract_tokens(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ["tokens", "tokenList", "list", "rows"]:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _token_symbol(token: dict[str, Any]) -> str | None:
    for key in ["symbol", "tokenSymbol", "asset", "baseAsset", "cexCoinName"]:
        value = token.get(key)
        if value:
            return _normalize_symbol(str(value))
    return None


def _alpha_trading_symbol(token: dict[str, Any], symbol: str) -> str:
    for key in ["tradingPair", "alphaSymbol", "pair", "tradeSymbol"]:
        value = token.get(key)
        if value:
            return str(value).upper()
    alpha_id = token.get("alphaId")
    if alpha_id:
        alpha = str(alpha_id).upper()
        return alpha if alpha.endswith("USDT") else f"{alpha}USDT"
    return f"{_normalize_symbol(symbol)}USDT"


def _request_json(url: str, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(url, params=params, timeout=8)
    response.raise_for_status()
    return response.json()


def get_alpha_tokens(force: bool = False) -> dict[str, dict[str, Any]]:
    global _tokens_cache, _tokens_loaded_at
    now = time.time()
    if not force and _tokens_cache is not None and now - _tokens_loaded_at < CACHE_TTL_SECONDS:
        return _tokens_cache

    payload = _request_json(ALPHA_TOKEN_LIST_URL)
    tokens: dict[str, dict[str, Any]] = {}
    for token in _extract_tokens(payload):
        symbol = _token_symbol(token)
        if symbol:
            token = {**token, "normalized_symbol": symbol}
            token["alpha_trading_symbol"] = _alpha_trading_symbol(token, symbol)
            tokens[symbol] = token
    _tokens_cache = tokens
    _tokens_loaded_at = now
    logger.info("[ALPHA] Token list loaded: %s tokens", len(tokens))
    return tokens


def fetch_alpha_symbol(symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    token = get_alpha_tokens().get(normalized)
    if not token:
        return {
            "valid": False,
            "symbol": normalized,
            "market_source": MARKET_SOURCE_UNKNOWN,
            "error": "Symbole introuvable sur Binance Alpha",
        }
    logger.info("[ALPHA] Token detected : %s", normalized)
    return {
        "valid": True,
        "symbol": normalized,
        "base_asset": normalized,
        "quote_asset": None,
        "market_source": MARKET_SOURCE_ALPHA,
        "alpha_trading_symbol": token["alpha_trading_symbol"],
        "token": token,
    }


def _extract_data(payload: Any) -> Any:
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def fetch_alpha_ticker(symbol: str) -> dict[str, Any]:
    resolved = fetch_alpha_symbol(symbol)
    if not resolved.get("valid"):
        return resolved
    normalized = resolved["symbol"]
    now = time.time()
    cached = _ticker_cache.get(normalized)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    logger.info("[ALPHA] Fetching Alpha data : %s", normalized)
    payload = _request_json(
        ALPHA_TICKER_URL,
        params={"symbol": resolved["alpha_trading_symbol"]},
    )
    data = _extract_data(payload)
    if not isinstance(data, dict):
        data = {}
    result = {
        **resolved,
        "ticker": data,
        "current_price": _safe_float(data.get("lastPrice") or data.get("price") or resolved["token"].get("price")),
        "price_change_pct": _safe_float(data.get("priceChangePercent") or resolved["token"].get("percentChange24h")),
        "volume": _safe_float(data.get("volume")),
        "quote_volume": _safe_float(data.get("quoteVolume") or resolved["token"].get("volume24h")),
        "market_cap": _safe_float(resolved["token"].get("marketCap")),
    }
    logger.info("[ALPHA] Price fetched : %s", result["current_price"])
    _ticker_cache[normalized] = (now, result)
    return result


def fetch_alpha_price(symbol: str) -> float | None:
    return fetch_alpha_ticker(symbol).get("current_price")


def get_alpha_price(symbol: str) -> float | None:
    return fetch_alpha_price(symbol)


def _parse_candle(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 6:
        return None
    open_time = int(float(raw[0]))
    close_time = int(float(raw[6])) if len(raw) > 6 and raw[6] is not None else open_time
    return {
        "open_time": open_time,
        "open": _safe_float(raw[1]),
        "high": _safe_float(raw[2]),
        "low": _safe_float(raw[3]),
        "close": _safe_float(raw[4]),
        "volume": _safe_float(raw[5]),
        "close_time": close_time,
        "quote_volume": _safe_float(raw[7]) if len(raw) > 7 else None,
        "trades": int(float(raw[8])) if len(raw) > 8 and raw[8] is not None else None,
    }


def fetch_alpha_candles(symbol: str, interval: str = "1h", limit: int = 120) -> list[dict[str, Any]]:
    resolved = fetch_alpha_symbol(symbol)
    if not resolved.get("valid"):
        return []
    normalized = resolved["symbol"]
    key = (normalized, interval, int(limit))
    now = time.time()
    cached = _candles_cache.get(key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    payload = _request_json(
        ALPHA_KLINES_URL,
        params={
            "symbol": resolved["alpha_trading_symbol"],
            "interval": interval,
            "limit": limit,
        },
    )
    data = _extract_data(payload)
    candles = [
        parsed
        for raw in (data if isinstance(data, list) else [])
        if (parsed := _parse_candle(raw)) is not None and parsed.get("close") is not None
    ]
    logger.info("[ALPHA] Candles available : %s", "yes" if candles else "no")
    _candles_cache[key] = (now, candles)
    return candles


def _moving_average(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return round(sum(values[-period:]) / period, 10)


def _alpha_analysis(candles: list[dict[str, Any]], ticker: dict[str, Any]) -> dict[str, Any]:
    closes = [float(candle["close"]) for candle in candles if candle.get("close") is not None]
    if not closes:
        return {
            "analysis_available": False,
            "limited_analysis": True,
            "global_trend": "UNKNOWN",
            "global_score": None,
            "setup_quality": "LIMITED",
            "market_error": "Analyse Alpha limitée",
        }

    current = closes[-1]
    previous = closes[-2] if len(closes) >= 2 else current
    ma7 = _moving_average(closes, 7)
    ma25 = _moving_average(closes, 25)
    ma99 = _moving_average(closes, 99)
    momentum_pct = round(((current - previous) / previous * 100), 4) if previous else 0
    change_pct = ticker.get("price_change_pct")

    if ma25 and current < ma25 and (ma99 is None or ma25 < ma99):
        trend = "BEARISH"
        score = 35
    elif ma25 and current > ma25 and (ma99 is None or ma25 > ma99):
        trend = "BULLISH"
        score = 62
    else:
        trend = "NEUTRAL"
        score = 50
    if change_pct is not None:
        score += max(-15, min(15, int(change_pct / 2)))
    if momentum_pct > 1:
        score += 5
    elif momentum_pct < -1:
        score -= 5
    score = max(0, min(100, score))

    return {
        "analysis_available": True,
        "limited_analysis": True,
        "technical_analysis_available": True,
        "global_trend": trend,
        "trend": trend,
        "global_score": score,
        "global_signal": "SURVEILLANCE",
        "setup_quality": "MOYEN",
        "trigger_label": "Alpha",
        "blocking_factor": "Analyse technique Alpha limitée",
        "alpha_ma7": ma7,
        "alpha_ma25": ma25,
        "alpha_ma99": ma99,
        "alpha_momentum_pct": momentum_pct,
        "market_error": "Analyse technique Alpha limitée",
    }


def fetch_alpha_market_data(symbol: str) -> dict[str, Any]:
    ticker = fetch_alpha_ticker(symbol)
    if not ticker.get("valid"):
        return ticker
    candles = []
    try:
        candles = fetch_alpha_candles(symbol, interval="1h", limit=120)
    except requests.RequestException:
        logger.info("[ALPHA] Candles available : no")
    analysis = _alpha_analysis(candles, ticker)
    return {
        **ticker,
        **analysis,
        "valid": True,
        "symbol": ticker["symbol"],
        "current_price": ticker.get("current_price"),
        "price_available": ticker.get("current_price") is not None,
        "candles_available": bool(candles),
        "candles": candles[-30:],
        "market_source": MARKET_SOURCE_ALPHA,
    }


def get_alpha_market_data(symbol: str) -> dict[str, Any]:
    return fetch_alpha_market_data(symbol)


def fetch_alpha_holdings() -> list[dict[str, Any]]:
    # Binance Alpha public market endpoints do not expose account holdings.
    # Portfolio quantities still come from Binance balances or manual positions.
    return []
