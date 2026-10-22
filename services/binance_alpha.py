from __future__ import annotations

import math
import re
import threading
import time
from typing import Any

import requests

from services.alpha_cache import AlphaCache
from services.binance_api_budget import ApiBudget, ApiQuotaError, budgeted_get
from services.market_data_errors import MarketDataError, market_state

ALPHA_BASE_URL = "https://www.binance.com"
ALPHA_TOKEN_LIST_URL = (
    f"{ALPHA_BASE_URL}/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"
)
ALPHA_TICKER_URL = f"{ALPHA_BASE_URL}/bapi/defi/v1/public/alpha-trade/ticker"
ALPHA_EXCHANGE_INFO_URL = f"{ALPHA_BASE_URL}/bapi/defi/v1/public/alpha-trade/get-exchange-info"
ALPHA_KLINES_URL = f"{ALPHA_BASE_URL}/bapi/defi/v1/public/alpha-trade/klines"
CACHE_TTL_SECONDS = 600
STALE_SECONDS = 1800
INTERVAL_SECONDS = {"1s": 1, "15s": 15, "1m": 60, "3m": 180, "5m": 300,
                    "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
                    "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200,
                    "1d": 86400, "3d": 259200, "1w": 604800, "1M": 2678400}
MARKET_SOURCE_SPOT = "BINANCE_SPOT"
MARKET_SOURCE_ALPHA = "BINANCE_ALPHA"
MARKET_SOURCE_MANUAL = "MANUAL"
MARKET_SOURCE_UNKNOWN = "UNKNOWN"

_clock = time.time
_sleep = time.sleep
_cache = AlphaCache(lambda: _clock())
_transport_slots = threading.BoundedSemaphore(2)


def clear_alpha_cache():
    _cache.clear()


def alpha_cache_metrics():
    return _cache.metrics()


def _normalize_symbol(symbol):
    raw = str(symbol or "").strip().upper().replace("/", "").replace("-", "")
    for suffix in ("USDC", "USDT"):
        if raw.endswith(suffix) and len(raw) > len(suffix):
            return raw[:-len(suffix)]
    return raw


def _safe_float(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _extract_data(payload):
    return payload.get("data") if isinstance(payload, dict) else None


def _quote_asset(pair):
    for quote in ("USDT", "USDC"):
        if pair.endswith(quote):
            return quote
    raise MarketDataError("INVALID_RESPONSE")


def _alpha_trading_symbol(token, symbol):
    pair = next((token[k] for k in ("tradingPair", "alphaSymbol", "pair", "tradeSymbol")
                 if token.get(k)), None)
    if pair is None:
        alpha_id = token.get("alphaId")
        if not isinstance(alpha_id, str) or not re.fullmatch(r"ALPHA_[0-9]{1,12}", alpha_id):
            raise MarketDataError("INVALID_RESPONSE")
        pair = alpha_id+"USDT"
    if not isinstance(pair, str) or not re.fullmatch(r"ALPHA_[0-9]{1,12}(USDT|USDC)", pair):
        raise MarketDataError("INVALID_RESPONSE")
    return pair


def _request_json(url, params=None):
    # Per-process concurrency is bounded; the durable budget also bounds bursts
    # across workers. Neither retries nor redirects can bypass reservation.
    if not _transport_slots.acquire(timeout=20):
        raise MarketDataError("BUSY", retry_after=1)
    try:
        try:
            budget = ApiBudget()
        except ValueError:
            raise MarketDataError("INVALID_CONFIGURATION") from None
        for attempt in range(2):
            try:
                response = budgeted_get(url, _budget=budget, params=params, timeout=8)
                status = getattr(response, "status_code", 200)
                if status >= 500:
                    raise requests.HTTPError("Alpha unavailable")
                try:
                    payload = response.json()
                except (ValueError, TypeError):
                    raise MarketDataError("INVALID_RESPONSE", retry_after=5) from None
                code = str(payload.get("code", "")).strip() if isinstance(payload, dict) else ""
                if code == "-1121":
                    raise MarketDataError("INVALID_PAIR", retry_after=CACHE_TTL_SECONDS)
                if status != 200:
                    raise MarketDataError("SERVICE_UNAVAILABLE", retry_after=5)
                if (not isinstance(payload, dict) or code not in {"000000", "0"}
                        or payload.get("success") is not True or "data" not in payload):
                    raise MarketDataError("INVALID_RESPONSE", retry_after=5)
                return payload
            except ApiQuotaError as error:
                if error.code == "LOCAL_QUOTA" and error.retry_after <= 1 and attempt == 0:
                    _sleep(error.retry_after)
                    continue
                code = {"ALPHA_NETWORK": "NETWORK_ERROR",
                        "ALPHA_UNAVAILABLE": "SERVICE_UNAVAILABLE",
                        "ALPHA_INVALID_RESPONSE": "INVALID_RESPONSE"}.get(error.code, "QUOTA")
                raise MarketDataError(code, retry_after=error.retry_after) from None
            except MarketDataError as error:
                if error.code != "INVALID_PAIR" and url != ALPHA_EXCHANGE_INFO_URL:
                    budget.suspend_alpha("ALPHA_INVALID_RESPONSE" if error.code == "INVALID_RESPONSE"
                                         else "ALPHA_UNAVAILABLE")
                raise error from None
            except ValueError:
                raise MarketDataError("INVALID_CONFIGURATION") from None
            except requests.RequestException as error:
                code = "SERVICE_UNAVAILABLE" if isinstance(error, requests.HTTPError) else "NETWORK_ERROR"
                if attempt == 0:
                    _sleep(.25)
                    continue
                budget.suspend_alpha("ALPHA_UNAVAILABLE" if code == "SERVICE_UNAVAILABLE" else "ALPHA_NETWORK")
                raise MarketDataError(code, retry_after=5) from None
    finally:
        _transport_slots.release()


def _load_tokens():
    data = _extract_data(_request_json(ALPHA_TOKEN_LIST_URL))
    if not isinstance(data, list) or not data:
        raise MarketDataError("INVALID_RESPONSE")
    tokens = {}
    for item in data:
        if not isinstance(item, dict):
            raise MarketDataError("INVALID_RESPONSE")
        symbol = item.get("symbol") or item.get("tokenSymbol")
        if not isinstance(symbol, str) or not symbol:
            raise MarketDataError("INVALID_RESPONSE")
        if not re.fullmatch(r"[A-Za-z0-9]{1,30}", symbol):
            # The application's resolver cannot address these symbols. A valid
            # Unicode/punctuated token must not break unrelated supported tokens.
            continue
        symbol = symbol.upper()
        pair = _alpha_trading_symbol(item, symbol)
        token = {**item, "normalized_symbol": symbol, "alpha_trading_symbol": pair}
        previous = tokens.get(symbol)
        candidates = previous.get("candidates", [previous]) if previous else []
        unique = {tuple(_identity(candidate).values()): candidate for candidate in candidates}
        unique[tuple(_identity(token).values())] = dict(token)
        token["candidates"] = list(unique.values())
        token["ambiguous"] = len(unique) > 1
        tokens[symbol] = token
    if not tokens:
        raise MarketDataError("INVALID_RESPONSE")
    return tokens


def _catalog(force=False):
    return _cache.get(("catalog",), _load_tokens, force=force)


def get_alpha_tokens(force=False):
    read = _catalog(force)
    if read.stale:
        # A plain mapping has no place to express staleness.
        raise read.error or MarketDataError("STALE_DATA")
    return read.value


def alpha_catalog_versions():
    return {name: _cache.status((name,)).get("signature") for name in ("catalog", "markets")}


def _negative_error(code, *, details=None):
    deadlines = [_cache.status((name,)).get("expires_at") for name in ("catalog", "markets")]
    remaining = [deadline-_clock() for deadline in deadlines if deadline is not None and deadline > _clock()]
    return MarketDataError(code, retry_after=min(remaining) if remaining else CACHE_TTL_SECONDS,
                           details={"catalog_versions": alpha_catalog_versions(), **(details or {})})


def _identity(token):
    result = {key: token.get(key) for key in ("alphaId", "chainId", "contractAddress")}
    pair = token.get("alpha_trading_symbol", "")
    if not result["alphaId"] and pair:
        result["alphaId"] = pair.removesuffix(_quote_asset(pair))
    return result


def get_alpha_candidates(symbol):
    read = _catalog()
    if read.stale:
        raise read.error or MarketDataError("STALE_DATA")
    token = read.value.get(_normalize_symbol(symbol))
    if token is None:
        return []
    return [{**_identity(candidate), "offline": candidate.get("offline"),
             "fullyDelisted": candidate.get("fullyDelisted"),
             "requires_explicit_selection": bool(token.get("ambiguous"))}
            for candidate in token.get("candidates", [token])]


def fetch_alpha_symbol(symbol, *, identity=None, allow_offline_conflict=False):
    normalized = _normalize_symbol(symbol)
    read = _catalog()
    token = read.value.get(normalized)
    if not token:
        if read.stale:
            raise read.error or MarketDataError("STALE_DATA")
        return {"valid": False, "symbol": normalized, "market_source": MARKET_SOURCE_UNKNOWN,
                **MarketDataError("NOT_FOUND").payload()}
    if token.get("ambiguous"):
        if not identity:
            candidates = get_alpha_candidates(normalized)
            code = "NO_MARKET" if all(c["offline"] is True or c["fullyDelisted"] is True for c in candidates) else "AMBIGUOUS_SYMBOL"
            raise _negative_error(code, details={"candidates": candidates})
    if identity is not None:
        if not isinstance(identity, dict) or not identity.get("alphaId"):
            raise MarketDataError("INVALID_CONFIGURATION")
        candidates = token.get("candidates", [token])
        matches = [candidate for candidate in candidates
                   if all(not _identity(candidate).get(key) or identity.get(key) == _identity(candidate)[key]
                          for key in ("alphaId", "chainId", "contractAddress"))]
        if len(matches) != 1:
            raise _negative_error("AMBIGUOUS_SYMBOL", details={"candidates": get_alpha_candidates(normalized)})
        token = matches[0]
    if read.stale and (token.get("offline") is True or token.get("fullyDelisted") is True):
        raise read.error or MarketDataError("STALE_DATA")
    pair = token["alpha_trading_symbol"]
    resolved = {"valid": True, "symbol": normalized, "base_asset": normalized,
                "quote_asset": _quote_asset(pair), "price_quote_asset": _quote_asset(pair),
                "market_source": MARKET_SOURCE_ALPHA, "alpha_trading_symbol": pair, "token": token,
                "alpha_identity": _identity(token),
                "token_status": {key: token.get(key) for key in ("offline", "fullyDelisted")},
                "catalog_state": "STALE" if read.stale else "FRESH"}
    if not read.stale and token.get("fullyDelisted") is True:
        raise _negative_error("DELISTED", details={"alpha_identity": _identity(token)})
    try:
        pairs = _pair_candidates(resolved)
    except MarketDataError:
        if not read.stale and token.get("offline") is True:
            raise _negative_error("OFFLINE", details={"alpha_identity": _identity(token)}) from None
        raise
    pair = pairs[0]
    resolved.update(alpha_trading_symbol=pair, quote_asset=_quote_asset(pair),
                    price_quote_asset=_quote_asset(pair), market_pairs=pairs)
    if not read.stale and token.get("offline") is True:
        # A fallback guess is not evidence of a TRADING market.
        status = _cache.status(("markets",))
        conflict = (status.get("expires_at") or 0) > _clock()
        error = _negative_error("OFFLINE_CONFLICT" if conflict else "OFFLINE",
                                details={"alpha_identity": _identity(token), "token_status": resolved["token_status"],
                                         "market_pairs": pairs if conflict else []})
        if not conflict or not allow_offline_conflict:
            raise error
        resolved["status_error"] = error
    return resolved


def _load_markets():
    data = _extract_data(_request_json(ALPHA_EXCHANGE_INFO_URL))
    if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
        raise MarketDataError("INVALID_RESPONSE")
    markets = {}
    for row in data["symbols"]:
        if not isinstance(row, dict):
            raise MarketDataError("INVALID_RESPONSE")
        base, quote, pair = row.get("baseAsset"), row.get("quoteAsset"), row.get("symbol")
        if quote not in {"USDT", "USDC"}:
            continue
        if not isinstance(base, str) or not re.fullmatch(r"ALPHA_[0-9]{1,12}", base) or pair != base+quote:
            raise MarketDataError("INVALID_RESPONSE")
        if row.get("status") == "TRADING":
            markets.setdefault(base, []).append(pair)
    return markets


def _pair_candidates(resolved):
    try:
        read = _cache.get(("markets",), _load_markets)
    except MarketDataError as error:
        if error.code != "INVALID_RESPONSE":
            raise
        read = None
    base = resolved["token"].get("alphaId") or resolved["alpha_trading_symbol"].removesuffix(resolved["quote_asset"])
    if read is not None and not read.stale:
        pairs = sorted(set(read.value.get(base, [])), key=lambda p: (not p.endswith("USDT"), p))
        if not pairs:
            raise _negative_error("NO_MARKET")
        return pairs
    return [base+"USDT", base+"USDC"]


def _select_ticker(resolved):
    for pair in _pair_candidates(resolved):
        candidate = {**resolved, "alpha_trading_symbol": pair,
                     "quote_asset": _quote_asset(pair), "price_quote_asset": _quote_asset(pair)}
        try:
            read = _ticker_read(candidate)
        except MarketDataError as error:
            if error.code != "INVALID_PAIR":
                raise
            continue
        resolved.update(candidate)
        return read
    raise _negative_error("NO_MARKET")


def _ticker_read(resolved):
    pair = resolved["alpha_trading_symbol"]

    def load():
        data = _extract_data(_request_json(ALPHA_TICKER_URL, {"symbol": pair}))
        if not isinstance(data, dict) or (data.get("symbol") is not None and data["symbol"] != pair):
            raise MarketDataError("INVALID_RESPONSE")
        price = _safe_float(data.get("lastPrice", data.get("price")))
        if price is None or price <= 0:
            # The undated token-list price is never a substitute for a quote.
            raise MarketDataError("INVALID_RESPONSE")
        as_of = _clock()
        basis = "RECEIVED_AT"
        if data.get("closeTime") is not None:
            stamp = _safe_float(data["closeTime"])
            if stamp is None or stamp <= 0 or stamp/1000 > as_of+5:
                raise MarketDataError("INVALID_RESPONSE")
            as_of, basis = stamp/1000, "EXCHANGE_TIME"
        result = {"ticker": data, "current_price": price, "as_of": as_of, "time_basis": basis}
        for name, source in (("price_change_pct", "priceChangePercent"), ("volume", "volume"),
                             ("quote_volume", "quoteVolume")):
            result[name] = _safe_float(data.get(source))
            if data.get(source) is not None and (result[name] is None
                    or name in {"volume", "quote_volume"} and result[name] < 0):
                raise MarketDataError("INVALID_RESPONSE")
        return result
    return _cache.get(("ticker", pair), load, valid_until=lambda value: value["as_of"]+CACHE_TTL_SECONDS,
                      source_stale_seconds=86400)


def _unavailable(symbol, error, resolved=None):
    return {**(resolved or {}), "valid": True if resolved else None,
            "symbol": _normalize_symbol(symbol), "market_source": MARKET_SOURCE_ALPHA,
            "current_price": None, "ticker": {}, "price_available": False,
            "analysis_available": False, "limited_analysis": True,
            "market_error": str(error), **error.payload(),
            "market_data": market_state("UNAVAILABLE", error=error, as_of=None, age_seconds=None,
                                       refresh_after=_clock()+(error.retry_after or 5),
                                       **error.details)}


def fetch_alpha_ticker(symbol, *, identity=None, verify_conflict=False):
    try:
        resolved = fetch_alpha_symbol(symbol, identity=identity, allow_offline_conflict=True)
    except MarketDataError as error:
        return _unavailable(symbol, error)
    if resolved.get("status_error"):
        error = resolved.pop("status_error")
        result = _unavailable(symbol, error, resolved)
        if verify_conflict:
            try:
                def verify():
                    read = _select_ticker(resolved)
                    return {"quote": read.value, "quote_asset": resolved["quote_asset"]}
                check = _cache.get(("conflict", resolved["alpha_trading_symbol"]), verify)
                quote = check.value["quote"]
                result["market_data"].update(last_known_price=quote["current_price"],
                    last_known_at=quote["as_of"], last_known_price_quote_asset=check.value["quote_asset"],
                    time_basis=quote["time_basis"], indicative_only=True, trading_authorized=False)
            except MarketDataError as failure:
                result["market_data"]["verification_error"] = failure.code
        return result
    if not resolved.get("valid"):
        error = MarketDataError("NOT_FOUND")
        return {**_unavailable(symbol, error), **resolved,
                "market_data": market_state("INVALID", error=error,
                                           refresh_after=_clock()+CACHE_TTL_SECONDS)}
    try:
        read = _select_ticker(resolved)
    except MarketDataError as error:
        return _unavailable(symbol, error, resolved)
    data = read.value
    age = max(0, _clock()-data["as_of"])
    stale = read.stale or age >= CACHE_TTL_SECONDS
    error = read.error or (MarketDataError("STALE_DATA") if stale else None)
    state = market_state("STALE" if stale else "FRESH", error=error,
                         as_of=data["as_of"], received_at=read.fetched_at,
                         time_basis=data["time_basis"], age_seconds=round(age, 3),
                         cached=read.cached,
                         refresh_after=(_clock()+(error.retry_after or 5) if stale else
                                        min(read.fetched_at, data["as_of"])+CACHE_TTL_SECONDS),
                         response_age_seconds=round(max(0, _clock()-read.fetched_at), 3),
                         cache_age_seconds=round(max(0, _clock()-read.fetched_at), 3),
                         quote_age_seconds=round(age, 3),
                         last_trade_age_seconds=None,
                         exchange_time_semantics="TICKER_WINDOW_END" if data["time_basis"] == "EXCHANGE_TIME" else None,
                         last_known_price=data["current_price"],
                         last_known_at=data["as_of"],
                         last_known_price_quote_asset=resolved["quote_asset"])
    if stale:
        # Keep last known values ONLY in labelled metadata, never in financial inputs.
        return {**_unavailable(symbol, error, resolved), "market_data": state}
    return {**resolved, **data, "market_data": state, "price_available": True,
            "market_cap": _safe_float(resolved["token"].get("marketCap"))}


def fetch_alpha_price(symbol):
    return fetch_alpha_ticker(symbol).get("current_price")


def get_alpha_price(symbol):
    return fetch_alpha_price(symbol)


def _parse_candle(raw, interval_seconds=3600):
    if not isinstance(raw, (list, tuple)) or len(raw) < 9:
        return None
    values = [_safe_float(v) for v in raw[:9]]
    if any(v is None for v in values):
        return None
    opened, op, high, low, close, volume, closed, quote_volume, trades = values
    if (opened < 0 or opened > (_clock()+5)*1000 or closed < opened
            or closed-opened > interval_seconds*1000
            or closed > (_clock()+interval_seconds+5)*1000
            or min(op, high, low, close) <= 0 or low > min(op, close)
            or high < max(op, close) or volume < 0 or quote_volume < 0
            or trades < 0 or not trades.is_integer()
            or not opened.is_integer() or not closed.is_integer()):
        return None
    return {"open_time": int(opened), "open": op, "high": high, "low": low, "close": close,
            "volume": volume, "close_time": int(closed), "quote_volume": quote_volume,
            "trades": int(trades)}


def _candles_read(resolved, interval, limit):
    if not isinstance(interval, str) or interval not in INTERVAL_SECONDS or type(limit) is not int or not 1 <= limit <= 1500:
        raise MarketDataError("INVALID_CONFIGURATION")
    pair = resolved["alpha_trading_symbol"]

    def load():
        data = _extract_data(_request_json(ALPHA_KLINES_URL,
                             {"symbol": pair, "interval": interval, "limit": limit}))
        if not isinstance(data, list):
            raise MarketDataError("INVALID_RESPONSE")
        candles = [_parse_candle(raw, INTERVAL_SECONDS[interval]) for raw in data]
        if any(row is None for row in candles) or any(
                b["open_time"] <= a["open_time"] for a, b in zip(candles, candles[1:])):
            raise MarketDataError("INVALID_RESPONSE")
        closes = [c["close"] for c in candles]
        if closes and (not math.isfinite(sum(closes)) or any(
                not math.isfinite(b/a) for a, b in zip(closes, closes[1:]))):
            raise MarketDataError("INVALID_RESPONSE")
        return candles
    read = _cache.get(("candles", pair, interval, limit), load)
    if read.value and read.value[-1]["close_time"]/1000 < _clock()-INTERVAL_SECONDS[interval]-CACHE_TTL_SECONDS:
        raise MarketDataError("STALE_DATA")
    return read


def fetch_alpha_candles(symbol, interval="1h", limit=120):
    resolved = fetch_alpha_symbol(symbol)
    if not resolved.get("valid"):
        return []
    _select_ticker(resolved)
    read = _candles_read(resolved, interval, limit)
    if read.stale:
        raise read.error or MarketDataError("STALE_DATA")
    return read.value


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



def fetch_alpha_market_data(symbol, *, identity=None, verify_conflict=False):
    ticker = fetch_alpha_ticker(symbol, identity=identity, verify_conflict=verify_conflict)
    if ticker.get("valid") is not True or not ticker.get("price_available"):
        return {**_alpha_analysis([], ticker), **ticker, "candles_available": False, "candles": []}
    error = None
    candles = []
    try:
        read = _candles_read(ticker, "1h", 120)
        if read.stale:
            raise read.error or MarketDataError("STALE_DATA")
        candles = read.value
    except MarketDataError as failure:
        error, candles = failure, []
    analysis = _alpha_analysis(candles, ticker)
    state = ticker["market_data"]
    if error or not candles:
        state = {**state, "state": "PARTIAL", "reason": error.code if error else "NO_CANDLES",
                 "retry_after": error.retry_after if error else None,
                 "refresh_after": min(state["refresh_after"], _clock()+(error.retry_after or 5))
                                  if error else state["refresh_after"]}
    return {**ticker, **analysis, "market_data": state, "valid": True,
            "price_available": True, "candles_available": bool(candles), "candles": candles[-30:]}


def get_alpha_market_data(symbol):
    return fetch_alpha_market_data(symbol)


def fetch_alpha_holdings():
    # Public market endpoints never expose account holdings.
    return []
