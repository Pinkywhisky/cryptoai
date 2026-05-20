from __future__ import annotations

import re
import time
from typing import Any

import requests

from services.binance_alpha import (
    MARKET_SOURCE_ALPHA,
    MARKET_SOURCE_SPOT,
    MARKET_SOURCE_UNKNOWN,
    get_alpha_market_data,
)


BASE_URL = "https://api.binance.com"
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,30}$")
CACHE_TTL_SECONDS = 6 * 60 * 60
COMMON_USDC_SYMBOLS = {
    "BTCUSDC",
    "ETHUSDC",
    "SOLUSDC",
    "XRPUSDC",
    "DOGEUSDC",
    "ADAUSDC",
    "SUIUSDC",
    "BNBUSDC",
    "LINKUSDC",
    "AVAXUSDC",
}

_symbol_cache: set[str] | None = None
_cache_loaded_at = 0.0


def clear_symbol_cache() -> None:
    global _symbol_cache, _cache_loaded_at
    _symbol_cache = None
    _cache_loaded_at = 0.0


def _normalize_candidate(symbol: str, quote_asset: str) -> tuple[str, str] | None:
    raw = str(symbol or "").strip().upper().replace("/", "").replace("-", "")
    quote_asset = quote_asset.upper()
    if not raw or not SYMBOL_RE.match(raw):
        return None
    if raw.endswith(quote_asset):
        base_asset = raw[: -len(quote_asset)]
        candidate = raw
    else:
        base_asset = raw
        candidate = f"{raw}{quote_asset}"
    if not base_asset or base_asset == quote_asset or not SYMBOL_RE.match(candidate):
        return None
    return candidate, base_asset


def _fetch_exchange_symbols() -> set[str]:
    response = requests.get(f"{BASE_URL}/api/v3/exchangeInfo", timeout=10)
    response.raise_for_status()
    payload = response.json()
    symbols = payload.get("symbols", [])
    return {
        item["symbol"].upper()
        for item in symbols
        if item.get("status") == "TRADING" and item.get("symbol")
    }


def get_exchange_symbols(force: bool = False) -> set[str]:
    global _symbol_cache, _cache_loaded_at
    now = time.time()
    if not force and _symbol_cache is not None and now - _cache_loaded_at < CACHE_TTL_SECONDS:
        return _symbol_cache
    _symbol_cache = _fetch_exchange_symbols()
    _cache_loaded_at = now
    return _symbol_cache


def validate_symbol(symbol: str, quote_asset: str = "USDC") -> dict[str, Any]:
    normalized = _normalize_candidate(symbol, quote_asset)
    if normalized is None:
        return {"valid": False, "error": "Invalid symbol"}

    candidate, base_asset = normalized
    if candidate in COMMON_USDC_SYMBOLS:
        return {
            "valid": True,
            "symbol": candidate,
            "base_asset": base_asset,
            "quote_asset": quote_asset.upper(),
            "market_source": MARKET_SOURCE_SPOT,
        }

    try:
        exchange_symbols = get_exchange_symbols()
    except requests.RequestException:
        return {"valid": False, "error": "Binance symbol validation unavailable"}

    if candidate not in exchange_symbols:
        return {"valid": False, "error": "Invalid symbol"}

    return {
        "valid": True,
        "symbol": candidate,
        "base_asset": base_asset,
        "quote_asset": quote_asset.upper(),
        "market_source": MARKET_SOURCE_SPOT,
    }


def resolve_market_symbol(symbol: str, quote_asset: str = "USDC") -> dict[str, Any]:
    raw = str(symbol or "").strip().upper().replace("/", "").replace("-", "")
    if not raw or not SYMBOL_RE.match(raw):
        return {
            "valid": False,
            "symbol": raw,
            "market_source": MARKET_SOURCE_UNKNOWN,
            "error": "Symbole introuvable sur Binance Spot et Binance Alpha",
        }

    spot = validate_symbol(raw, quote_asset=quote_asset)
    if spot.get("valid"):
        return {
            **spot,
            "resolved_symbol": spot["symbol"],
            "market_source": MARKET_SOURCE_SPOT,
        }

    try:
        alpha = get_alpha_market_data(raw)
    except requests.RequestException:
        alpha = {"valid": False}
    except Exception:
        alpha = {"valid": False}

    if alpha.get("valid"):
        resolved = str(alpha.get("symbol") or raw).upper()
        return {
            "valid": True,
            "symbol": resolved,
            "resolved_symbol": resolved,
            "base_asset": resolved,
            "quote_asset": None,
            "market_source": MARKET_SOURCE_ALPHA,
        }

    return {
        "valid": False,
        "symbol": raw,
        "market_source": MARKET_SOURCE_UNKNOWN,
        "error": "Symbole introuvable sur Binance Spot et Binance Alpha",
    }
