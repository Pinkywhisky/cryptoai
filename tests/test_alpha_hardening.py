"""Alpha integration tests: real durable budget, fake public HTTP, temporary SQLite."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import format_datetime
import json
import threading
import time

import pytest
import requests

from services import binance_alpha as alpha, symbols, personal_data, market_cache
from services.alpha_cache import AlphaCache
from services.binance_api_budget import ApiBudget, ApiQuotaError, budgeted_get, endpoint_weight
from services.market_data_errors import MarketDataError


class Clock:
    def __init__(self):
        self.now = 1800000000.
        self.guard = threading.Lock()

    def __call__(self):
        with self.guard:
            return self.now

    def advance(self, seconds):
        with self.guard:
            self.now += seconds


class Response:
    def __init__(self, data=None, status=200, headers=None, envelope=True):
        self.status_code = status
        self.headers = headers or {}
        self.data = {"code": "000000", "success": True, "data": data} if envelope else data

    def json(self):
        return deepcopy(self.data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("fake HTTP failure")


class PublicAlpha:
    def __init__(self, clock):
        self.clock = clock
        self.count = 10
        self.calls = []
        self.failures = {}
        self.active = 0
        self.maximum_active = 0
        self.delay = 0
        self.guard = threading.Lock()

    def candles(self, interval="1h"):
        now = int(self.clock()*1000)
        duration = alpha.INTERVAL_SECONDS[interval]*1000
        return [[now-(120-i)*duration, "2", "5", "1", str(2+i/100), "100",
                 now-(119-i)*duration-1, "300", "5", "50", "100", "0"] for i in range(120)]

    def get(self, url, **kwargs):
        assert url.startswith(("https://www.binance.com/bapi/", "https://api.binance.com/api/v3/"))
        assert kwargs["allow_redirects"] is False
        assert not any(key.lower() in {"headers", "auth"} for key in kwargs)
        params = kwargs.get("params") or {}
        kind = "catalog" if "token/list" in url else "candles" if "klines" in url else "ticker"
        if "get-exchange-info" in url:
            kind = "markets"
        if "exchangeInfo" in url:
            kind = "spot"
        with self.guard:
            self.calls.append((kind, params.get("symbol")))
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            override = self.failures.get((kind, params.get("symbol")), self.failures.get(kind))
            if isinstance(override, list):
                override = override.pop(0) if override else None
        try:
            if self.delay:
                time.sleep(self.delay)
            if isinstance(override, Exception):
                raise override
            if override is not None:
                return override
            if kind == "catalog":
                data = [{"symbol": "TOK"+str(i), "alphaId": "ALPHA_"+str(i), "price": "999", "marketCap": "1000000"}
                        for i in range(self.count)]
            elif kind == "markets":
                data = {"symbols": [{"baseAsset": f"ALPHA_{i}", "quoteAsset": "USDT", "symbol": f"ALPHA_{i}USDT", "status": "TRADING"} for i in range(self.count)]}
            elif kind == "candles":
                data = self.candles(params.get("interval", "1h"))
            elif kind == "spot":
                data = {"symbols": [{"symbol": "BTCUSDC", "status": "TRADING"}]}
                return Response(data, envelope=False)
            else:
                data = {"symbol": params["symbol"], "lastPrice": "3.25",
                        "priceChangePercent": "5", "quoteVolume": "1000",
                        "closeTime": int(self.clock()*1000)}
            return Response(data)
        finally:
            with self.guard:
                self.active -= 1


@pytest.fixture
def rig(monkeypatch, tmp_path):
    clock = Clock()
    budget = ApiBudget(tmp_path/"alpha.db", clock=clock)
    remote = PublicAlpha(clock)
    monkeypatch.setattr(alpha, "_clock", clock)
    monkeypatch.setattr(alpha, "_sleep", clock.advance)
    monkeypatch.setattr(alpha, "ApiBudget", lambda: budget)
    monkeypatch.setattr(requests, "get", remote.get)
    monkeypatch.setattr(symbols, "get_exchange_symbols", lambda force=False: {"BTCUSDC"})
    return clock, budget, remote


@pytest.mark.parametrize("count", [1, 2, 3, 6, 10])
def test_followed_tokens_cold_warm_and_three_refresh_cycles(rig, count):
    clock, budget, remote = rig
    remote.count = count
    for cycle in range(3):
        start = len(remote.calls)
        results = [alpha.get_alpha_market_data("TOK"+str(i)) for i in range(count)]
        assert all(r["valid"] is True and r["candles_available"] and r["price_available"] for r in results)
        assert len(remote.calls)-start == 2+2*count
        for i in range(count):
            assert alpha.get_alpha_market_data("TOK"+str(i))["market_data"]["cached"] is True
        assert len(remote.calls)-start == 2+2*count
        clock.advance(601)
    assert budget.metrics("ALPHA")["weight"] == 3*(2+2*count)
    assert budget.metrics("SPOT")["requests"] == 0


def test_resolution_fetches_metadata_only_and_shares_catalog(rig):
    _, _, remote = rig
    assert symbols.resolve_market_symbol("TOK0")["market_source"] == "BINANCE_ALPHA"
    assert symbols.resolve_market_symbol("TOK1")["valid"] is True
    assert remote.calls == [("catalog", None), ("markets", None)]


def test_real_absence_requires_fresh_validated_catalog(rig):
    clock, _, remote = rig
    assert symbols.resolve_market_symbol("MISSING")["error_code"] == "NOT_FOUND"
    clock.advance(601)
    remote.failures["catalog"] = requests.Timeout("SECRET_REMOTE_TEXT")
    result = symbols.resolve_market_symbol("MISSING")
    assert result["valid"] is None
    assert result["error_code"] == "NETWORK_ERROR"
    assert "introuvable" not in result["error"].lower()
    assert "SECRET_REMOTE_TEXT" not in json.dumps(result)


@pytest.mark.parametrize("error,code", [
    (requests.Timeout("secret"), "NETWORK_ERROR"),
    (requests.ConnectionError("secret"), "NETWORK_ERROR"),
    (Response(status=503), "SERVICE_UNAVAILABLE"),
    (Response(status=403), "SERVICE_UNAVAILABLE"),
    (Response({"message": "secret"}, envelope=False), "INVALID_RESPONSE"),
    (Response([], status=200, envelope=False), "INVALID_RESPONSE"),
])
def test_catalog_outages_are_never_invalid_symbols(rig, error, code):
    _, _, remote = rig
    remote.failures["catalog"] = error
    result = symbols.resolve_market_symbol("TOK0")
    assert result["valid"] is None and result["error_code"] == code
    assert len(remote.calls) <= 2
    assert alpha.get_alpha_market_data("TOK1")["valid"] is None
    assert len(remote.calls) <= 2


@pytest.mark.parametrize("status,wait", [(429, 17), (418, 120)])
def test_retry_after_is_durable_and_not_retried(rig, status, wait):
    clock, budget, remote = rig
    remote.failures["catalog"] = Response(status=status, headers={"Retry-After": str(wait)})
    result = symbols.resolve_market_symbol("TOK0")
    assert result["valid"] is None and result["error_code"] == "QUOTA"
    assert result["retry_after"] == wait
    assert len(remote.calls) == 1
    assert ApiBudget(budget.path, clock=clock).metrics("ALPHA")["retry_after"] == wait
    alpha.clear_alpha_cache()
    assert symbols.resolve_market_symbol("TOK1")["error_code"] == "QUOTA"
    assert len(remote.calls) == 1
    clock.advance(wait+1)
    remote.failures.clear()
    assert symbols.resolve_market_symbol("TOK0")["valid"] is True


def test_retry_after_http_date_and_invalid_fallback(rig):
    clock, budget, remote = rig
    until = format_datetime(datetime.fromtimestamp(clock()+30, timezone.utc))
    remote.failures["catalog"] = Response(status=429, headers={"Retry-After": until})
    assert symbols.resolve_market_symbol("TOK0")["retry_after"] == 30
    clock.advance(31)
    remote.failures["catalog"] = Response(status=429, headers={"Retry-After": "invalid"})
    assert symbols.resolve_market_symbol("TOK1")["retry_after"] == 60


@pytest.mark.parametrize("remaining,reset,blocked", [("0", "7", True), ("3", "7", False),
                                                     ("0", "nan", False), ("0", "999999", False)])
def test_optional_exhausted_headers_never_import_spot_limits(rig, remaining, reset, blocked):
    _, budget, remote = rig
    remote.failures["catalog"] = Response([{"symbol": "TOK0", "alphaId": "ALPHA_0"}],
        headers={"RateLimit-Remaining": remaining, "RateLimit-Reset": reset,
                 "X-MBX-USED-WEIGHT-1m": "5999"})
    assert alpha.get_alpha_tokens()["TOK0"]["alphaId"] == "ALPHA_0"
    state = budget.metrics("ALPHA")
    assert bool(state["retry_after"]) is blocked
    assert all(x["source"] != "EXCHANGE_INFO" for x in state["limits"])
    assert not any(k.startswith("REQUEST_WEIGHT") for k in state["windows"])


def test_low_and_high_independent_alpha_configuration(rig, monkeypatch):
    clock, budget, remote = rig
    monkeypatch.setenv("CRYPTOAI_API_ALPHA_RAW_REQUESTS", "4")  # 3 requests after margin
    monkeypatch.setenv("CRYPTOAI_API_ALPHA_WEIGHT_PER_MINUTE", "1")  # retired estimate ignored
    assert alpha.get_alpha_market_data("TOK0")["price_available"] is True
    result = alpha.get_alpha_market_data("TOK1")
    assert result["market_data"]["reason"] == "QUOTA"
    assert result["valid"] is True
    assert len(remote.calls) == 3
    clock.advance(61)
    monkeypatch.setenv("CRYPTOAI_API_ALPHA_RAW_REQUESTS", "300")
    for i in range(10):
        assert alpha.get_alpha_market_data("TOK"+str(i))["price_available"] is True
    assert budget.metrics("SPOT")["requests"] == 0
    assert next(x for x in budget.metrics("ALPHA")["limits"] if x["seconds"] == 60)["limit"] == 300


def test_concurrent_same_token_single_flight_and_spot_coexistence(rig):
    _, budget, remote = rig
    remote.delay = .015
    def read(index):
        if index % 4 == 0:
            return budgeted_get("https://api.binance.com/api/v3/exchangeInfo", _budget=budget).json()
        return alpha.get_alpha_market_data("TOK0")
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(read, range(32)))
    assert all(r.get("symbols") or r.get("price_available") for r in results)
    assert Counter(k for k, _ in remote.calls) == {"spot": 8, "catalog": 1, "ticker": 1, "candles": 1, "markets": 1}
    assert budget.metrics("SPOT")["weight"] == 160
    assert budget.metrics("ALPHA")["requests"] == 4


def test_different_tokens_transport_concurrency_is_bounded(rig, monkeypatch):
    _, _, remote = rig
    remote.delay = .015
    monkeypatch.setenv("CRYPTOAI_API_ALPHA_BURST_PER_SECOND", "100")
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(alpha.get_alpha_market_data, ["TOK"+str(i) for i in range(10)]))
    assert all(r["candles_available"] for r in results)
    assert remote.maximum_active <= 2
    assert len(remote.calls) == 22


def test_ten_token_outage_has_shared_short_circuit_and_recovers(rig):
    clock, _, remote = rig
    alpha.fetch_alpha_symbol("TOK0")
    remote.failures["ticker"] = requests.Timeout("secret")
    rows = [alpha.get_alpha_market_data("TOK"+str(i)) for i in range(10)]
    assert all(r["valid"] is True and r["current_price"] is None for r in rows)
    assert Counter(k for k, _ in remote.calls) == {"catalog": 1, "markets": 1, "ticker": 2}
    clock.advance(61)
    remote.failures.clear()
    assert all(alpha.get_alpha_market_data("TOK"+str(i))["price_available"] for i in range(10))


def test_expired_price_is_only_labelled_metadata_and_not_financial_input(rig, monkeypatch):
    clock, _, remote = rig
    fresh = alpha.get_alpha_market_data("TOK0")
    old_time = fresh["market_data"]["as_of"]
    clock.advance(601)
    remote.failures["ticker"] = requests.Timeout("secret")
    stale = alpha.get_alpha_market_data("TOK0")
    assert stale["market_data"]["state"] == "STALE"
    assert stale["current_price"] is None and stale["ticker"] == {}
    assert stale["market_data"]["last_known_price"] == 3.25
    assert stale["market_data"]["last_known_price_quote_asset"] == "USDT"
    assert stale["market_data"]["as_of"] == old_time
    assert stale.get("global_score") is None and not stale["analysis_available"]
    monkeypatch.setattr(personal_data, "get_manual_fx_rate", lambda quote: pytest.fail("No stale FX valuation"))
    values = personal_data._manual_financial_values(
        {"symbol": "TOK0", "quantity": 2, "invested_amount": 4}, stale)
    assert values["current_price"] is None and values["gain_loss_amount"] is None
    before = len(remote.calls)
    assert alpha.get_alpha_market_data("TOK0")["market_data"]["state"] == "STALE"
    assert len(remote.calls) == before
    clock.advance(61)
    remote.failures.clear()
    assert alpha.get_alpha_market_data("TOK0")["market_data"]["state"] == "FRESH"


def test_cache_expires_at_ten_minutes_and_discard_old_stale_values(rig):
    clock, _, remote = rig
    alpha.fetch_alpha_ticker("TOK0")
    before = len(remote.calls)
    clock.advance(599)
    assert alpha.fetch_alpha_ticker("TOK0")["market_data"]["cached"] is True
    assert len(remote.calls) == before
    clock.advance(1)
    assert alpha.fetch_alpha_ticker("TOK0")["market_data"]["cached"] is False
    assert len(remote.calls) == before+3
    clock.advance(2401)
    remote.failures["ticker"] = requests.Timeout()
    result = alpha.fetch_alpha_ticker("TOK0")
    assert result["market_data"]["state"] == "UNAVAILABLE"
    assert result["market_data"].get("last_known_price") is None


def test_server_timestamp_not_receipt_controls_quote_freshness(rig):
    clock, _, remote = rig
    remote.failures["ticker"] = Response({"lastPrice": "3", "closeTime": (clock()-601)*1000})
    result = alpha.get_alpha_market_data("TOK0")
    assert result["market_data"]["state"] == "STALE" and result["current_price"] is None
    assert result["market_data"]["time_basis"] == "EXCHANGE_TIME"


def test_missing_server_time_is_explicitly_receipt_time(rig):
    _, _, remote = rig
    remote.failures["ticker"] = Response({"lastPrice": "3"})
    assert alpha.fetch_alpha_ticker("TOK0")["market_data"]["time_basis"] == "RECEIVED_AT"


@pytest.mark.parametrize("data", [{}, {"lastPrice": "NaN"}, {"lastPrice": "Infinity"},
    {"lastPrice": True}, {"lastPrice": "-2"}, {"lastPrice": "0"},
    {"lastPrice": "1", "closeTime": "bad"}, {"lastPrice": "1", "symbol": "ALPHA_9USDT"}])
def test_bad_ticker_never_falls_back_to_undated_catalog_price(rig, data):
    _, _, remote = rig
    remote.failures["ticker"] = Response(data)
    result = alpha.get_alpha_market_data("TOK0")
    assert result["current_price"] is None and result["market_data"]["reason"] == "INVALID_RESPONSE"
    assert result.get("global_score") is None


@pytest.mark.parametrize("data", [None, {}, [], [{"symbol": "TOK0"}],
    [{"symbol": "TOK0", "alphaId": "invalid"}], ["bad"]])
def test_malformed_catalog_cannot_confirm_absence(rig, data):
    _, _, remote = rig
    remote.failures["catalog"] = Response(data)
    assert symbols.resolve_market_symbol("MISSING")["valid"] is None


def test_ambiguous_catalog_symbol_is_never_silently_last_wins(rig):
    _, _, remote = rig
    remote.failures["catalog"] = Response([
        {"symbol": "TOK0", "alphaId": "ALPHA_1"}, {"symbol": "TOK0", "alphaId": "ALPHA_2"},
        {"symbol": "TOK0", "alphaId": "ALPHA_2"}])
    result = symbols.resolve_market_symbol("TOK0")
    assert result["valid"] is None and result["error_code"] == "AMBIGUOUS_SYMBOL"


@pytest.mark.parametrize("failure,reason", [
    (requests.Timeout("secret"), "NETWORK_ERROR"),
    (Response({"unexpected": True}), "INVALID_RESPONSE"),
    (Response([[0, "1", "2", "0", "nan", "5", 10, "10", "1"]]), "INVALID_RESPONSE"),
])
def test_unavailable_candles_keep_fresh_price_without_analysis(rig, failure, reason):
    _, _, remote = rig
    remote.failures["candles"] = failure
    result = alpha.get_alpha_market_data("TOK0")
    assert result["current_price"] == 3.25 and result["price_available"]
    assert result["market_data"]["state"] == "PARTIAL" and result["market_data"]["reason"] == reason
    assert not result["candles_available"] and result["global_score"] is None


def test_old_candles_cannot_create_current_technical_analysis(rig):
    clock, _, remote = rig
    candles = remote.candles()
    for row in candles:
        row[0] -= 86400000
        row[6] -= 86400000
    remote.failures["candles"] = Response(candles)
    result = alpha.get_alpha_market_data("TOK0")
    assert result["price_available"] and result["market_data"]["reason"] == "STALE_DATA"
    assert not result["candles_available"]


def test_cache_copy_isolation_lru_and_failure_timestamp():
    clock = Clock()
    cache = AlphaCache(clock, maximum=2)
    a = cache.get("a", lambda: {"price": 1})
    a.value["price"] = 999
    assert cache.get("a", lambda: None).value == {"price": 1}
    cache.get("b", lambda: 2)
    cache.get("c", lambda: 3)
    assert cache.metrics()["entries"] == 2
    clock.advance(601)
    def fail():
        raise MarketDataError("QUOTA", retry_after=80)
    stale = cache.get("b", fail)
    assert stale.stale and stale.fetched_at == 1800000000
    assert stale.error.retry_after == 80


def test_failed_catalog_cannot_poison_previous_good_snapshot(rig):
    clock, _, remote = rig
    alpha.fetch_alpha_symbol("TOK0")
    clock.advance(601)
    remote.failures["catalog"] = Response({})
    assert alpha.fetch_alpha_symbol("TOK0")["catalog_state"] == "STALE"
    assert symbols.resolve_market_symbol("NOTREAL")["valid"] is None
    clock.advance(61)
    remote.failures.clear()
    assert alpha.fetch_alpha_symbol("TOK0")["catalog_state"] == "FRESH"


def test_spot_outage_does_not_reclassify_and_known_alpha_remains_readable(rig, monkeypatch):
    _, _, remote = rig
    def fail(force=False):
        raise requests.Timeout()
    monkeypatch.setattr(symbols, "get_exchange_symbols", fail)
    assert symbols.resolve_market_symbol("TOK0")["service"] == "SPOT"
    assert remote.calls == []
    result = personal_data.get_market_snapshot("TOK0", market_source="BINANCE_ALPHA")
    assert result["is_valid_symbol"] is True and result["price_available"]


def test_retired_alpha_weight_policy_preserves_raw_counts_and_ban(rig):
    clock, budget, _ = rig
    import sqlite3
    from services.history import init_db
    init_db(budget.path)
    state = {"policy_version": 2, "limits": [{"metric": "REQUEST_WEIGHT", "seconds": 60,
               "limit": 600, "source": "CONSERVATIVE_FALLBACK"}],
        "windows": {"REQUEST_WEIGHT:60": {"start": clock(), "used": 480},
                    "RAW_REQUESTS:60": {"start": clock(), "used": 6}},
        "blocked_until": clock()+30, "block_reason": "HTTP_418", "requests": 6, "weight": 480,
        "by_endpoint": {"ticker": {"requests": 6, "weight": 480}},
        "outstanding": {}, "published": False, "limits_at": None,
        "missing_headers": 6, "invalid_headers": 0}
    with sqlite3.connect(budget.path) as connection:
        connection.execute("INSERT INTO binance_api_state VALUES(?,?)", ("ALPHA", json.dumps(state)))
    current = budget.metrics("ALPHA")
    assert current["weight"] == current["requests"] == 6 and current["retry_after"] == 30
    assert current["windows"]["RAW_REQUESTS:60"]["used"] == 6
    with pytest.raises(ApiQuotaError):
        budget.reserve(alpha.ALPHA_TICKER_URL)


def test_alpha_budget_failure_does_not_block_spot(rig):
    _, budget, remote = rig
    remote.failures["catalog"] = Response(status=418, headers={"Retry-After": "100"})
    assert symbols.resolve_market_symbol("TOK0")["error_code"] == "QUOTA"
    assert budgeted_get("https://api.binance.com/api/v3/exchangeInfo", _budget=budget).status_code == 200
    assert budget.metrics("SPOT")["retry_after"] == 0


@pytest.mark.parametrize("code,status", [("QUOTA", 429), ("NETWORK_ERROR", 503),
    ("SERVICE_UNAVAILABLE", 503), ("INVALID_RESPONSE", 503), ("AMBIGUOUS_SYMBOL", 400)])
@pytest.mark.parametrize("route", ["/api/positions", "/api/watch-candidates"])
def test_authenticated_writes_report_transient_errors_without_saving(rig, monkeypatch, code, status, route):
    import main
    from tests.auth_client import AuthenticatedClient
    resolution = {"valid": None, **MarketDataError(code, retry_after=13).payload()}
    monkeypatch.setattr(personal_data, "resolve_market_symbol", lambda symbol: resolution)
    with AuthenticatedClient(main.app) as client:
        payload = {"symbol": "TOK0"}
        if route == "/api/positions":
            payload.update(quantity=1, average_buy_price=1, invested_amount=1)
        response = client.post(route, json=payload)
    assert response.status_code == status
    assert response.json()["error_code"] == code and response.headers["Retry-After"] == "13"
    assert personal_data.list_positions(enrich=False) == []
    assert personal_data.list_watch_candidates(enrich=False) == []


def test_anonymous_and_missing_csrf_never_reach_alpha(rig):
    import main
    from fastapi.testclient import TestClient
    from tests.auth_client import AuthenticatedClient
    _, _, remote = rig
    with TestClient(main.app, base_url="http://localhost:8000") as client:
        assert client.get("/api/market").status_code == 401
        assert client.post("/api/watch-candidates", json={"symbol": "TOK0"}).status_code == 401
    with AuthenticatedClient(main.app) as client:
        client.headers.pop("X-CSRF-Token", None)
        # The helper inserts its normal CSRF header per request; use the raw base
        # implementation to verify production middleware without that convenience.
        response = TestClient.request(client, "POST", "/api/watch-candidates",
                                      json={"symbol": "TOK0"}, headers={"X-CSRF-Token": "wrong"})
        assert response.status_code == 403
    assert remote.calls == []


def test_cleanup_and_repair_defer_outages_without_deleting_or_reclassifying(rig):
    clock, _, remote = rig
    personal_data.create_watch_candidate({"symbol": "TOK0"})
    personal_data.create_watch_candidate({"symbol": "TOK1"})
    personal_data.create_position({"symbol": "TOK2", "source": "BINANCE", "quantity": 2})
    before_watch = personal_data.list_watch_candidates(enrich=False)
    before_positions = personal_data.list_positions(enrich=False)
    clock.advance(2401)
    remote.failures["catalog"] = requests.Timeout()
    cleaned = personal_data.cleanup_invalid_symbols()
    repaired = personal_data.repair_market_sources()
    assert cleaned["removed"] == 0 and cleaned["deferred"] == 3
    assert repaired["updated"] == 0 and repaired["deferred"] == 3
    assert personal_data.list_watch_candidates(enrich=False) == before_watch
    assert personal_data.list_positions(enrich=False) == before_positions


def test_confirmed_invalid_cleanup_still_works_without_budget_transaction_deadlock(rig, monkeypatch):
    personal_data.create_watch_candidate({"symbol": "TOK0"})
    personal_data.create_watch_candidate({"symbol": "TOK1"})
    alpha.clear_alpha_cache()
    _, _, remote = rig
    remote.failures["catalog"] = Response([{"symbol": "TOK9", "alphaId": "ALPHA_9"}])
    assert personal_data.cleanup_invalid_symbols()["watch_removed"] == 2


def test_cockpit_propagates_current_stale_and_recovery_without_buy_signal(rig, monkeypatch):
    import main
    clock, _, remote = rig
    monkeypatch.setattr(main, "get_market_candidates", lambda **kwargs: [])
    personal_data.create_watch_candidate({"symbol": "TOK0"})
    fresh = main.api_market(force=True)
    row = fresh["results"][0]
    assert row["market_data"]["state"] == "FRESH"
    assert row["decision_engine"]["decision"] == "WAIT"
    assert row["decision_engine"]["timing_is_actionable"] is False
    monkeypatch.setattr(market_cache.time, "time", clock)
    # Outer cache uses the nested Alpha deadline, not its 15-minute global age.
    assert market_cache.get_market_payload_cache() is not None
    clock.advance(601)
    assert market_cache.get_market_payload_cache() is None
    remote.failures["ticker"] = requests.Timeout()
    stale = main.api_market()["results"][0]
    assert stale["market_data"]["state"] == "STALE" and stale["current_price"] is None
    assert stale["global_score"] is None and stale["decision_engine"]["positive_factors"] == []
    clock.advance(61)
    remote.failures.clear()
    assert main.api_market()["results"][0]["market_data"]["state"] == "FRESH"


def test_synced_accounting_snapshot_stays_dated_when_alpha_quote_stales(rig):
    import main
    clock, _, remote = rig
    position = {"symbol": "TOK0", "synced_from_binance": True, "market_source": "BINANCE_ALPHA",
                "quantity": 4, "current_price": 2, "current_value": 8,
                "unrealized_pnl": 3, "profit_loss_pct": 60, "last_sync_at": "2026-01-01T00:00:00Z",
                "source": "BINANCE", "status": "ACTIVE", "is_active": 1}
    personal_data.enrich_position(position)
    clock.advance(601)
    remote.failures["ticker"] = requests.Timeout()
    enriched = personal_data.enrich_position(position)
    assert enriched["current_value"] == 8 and enriched["unrealized_pnl"] == 3
    assert enriched["valuation_as_of"] == position["last_sync_at"]
    universe = main.get_market_universe(positions=[enriched])
    row = main.build_alpha_market_row(universe[0])
    assert row["current_price"] is None and row["global_score"] is None


def test_payload_cache_cannot_reuse_legacy_alpha_without_freshness(tmp_path):
    path = tmp_path/"cache.db"
    market_cache.save_market_payload({"results": [{"symbol": "TOK0", "sources": ["BINANCE_ALPHA"],
        "watch_info": {"market_source": "BINANCE_ALPHA", "current_price": 2}}]}, path)
    assert market_cache.get_market_payload_cache(path) is None


def test_endpoint_accounting_unit_is_local_requests_not_fictional_weight(rig):
    _, budget, _ = rig
    for url in (alpha.ALPHA_TOKEN_LIST_URL, alpha.ALPHA_TICKER_URL, alpha.ALPHA_KLINES_URL):
        from urllib.parse import urlsplit
        assert endpoint_weight(urlsplit(url).path) == 1
    alpha.get_alpha_market_data("TOK0")
    metrics = budget.metrics("ALPHA")
    assert metrics["accounting_unit"] == "LOCAL_REQUESTS"
    assert metrics["requests"] == metrics["weight"] == 4


@pytest.mark.parametrize("payload", [
    {"code": "12345", "success": False, "message": "SECRET", "data": []},
    {"code": "000000", "success": "true", "data": []},
    {"code": "000000", "success": True},
])
def test_business_error_envelope_is_sanitized(rig, caplog, payload):
    _, _, remote = rig
    remote.failures["catalog"] = Response(payload, envelope=False)
    result = symbols.resolve_market_symbol("TOK0")
    assert result["valid"] is None and result["error_code"] == "INVALID_RESPONSE"
    assert "SECRET" not in json.dumps(result)+caplog.text


def test_legacy_spot_validator_does_not_label_network_failure_invalid(rig, monkeypatch):
    monkeypatch.setattr(symbols, "COMMON_USDC_SYMBOLS", set())
    def fail(force=False):
        raise requests.Timeout()
    monkeypatch.setattr(symbols, "get_exchange_symbols", fail)
    assert symbols.validate_symbol("TOK0")["valid"] is None


def test_confirmed_missing_known_alpha_record_keeps_explicit_invalid_state(rig):
    _, _, remote = rig
    remote.failures["catalog"] = Response([{"symbol": "TOK9", "alphaId": "ALPHA_9"}])
    snapshot = personal_data.get_market_snapshot("TOK0", market_source="BINANCE_ALPHA")
    assert snapshot["is_valid_symbol"] is False and snapshot["market_source"] == "BINANCE_ALPHA"
    assert snapshot["market_data"]["state"] == "INVALID" and snapshot["current_price"] is None


@pytest.mark.parametrize("setting", ["CRYPTOAI_API_ALPHA_RAW_REQUESTS", "CRYPTOAI_API_ALPHA_BURST_PER_SECOND"])
def test_invalid_alpha_config_fails_closed_with_structured_error(rig, monkeypatch, setting):
    monkeypatch.setenv(setting, "bad")
    result = symbols.resolve_market_symbol("TOK0")
    assert result["valid"] is None and result["error_code"] == "INVALID_CONFIGURATION"
    assert rig[2].calls == []


@pytest.mark.parametrize("interval", ["1m", "15m", "4h", "1d", "1M"])
def test_supported_alpha_candle_intervals_are_preserved(rig, interval):
    assert len(alpha.fetch_alpha_candles("TOK0", interval=interval)) == 120


def test_future_and_numerically_unusable_candles_are_rejected(rig):
    clock, _, remote = rig
    for bad in [
        [[(clock()+60)*1000, "1", "2", ".5", "1", "5", (clock()+120)*1000, "10", "2"]],
        [[(clock()-60)*1000, "1e308", "1e308", "1e308", "1e308", "5",
          (clock()-30)*1000, "10", "2"],
         [(clock()-29)*1000, "1e308", "1e308", "1e308", "1e308", "5",
          clock()*1000, "10", "2"]],
    ]:
        alpha.clear_alpha_cache()
        remote.failures["candles"] = Response(bad)
        result = alpha.get_alpha_market_data("TOK0")
        assert result["price_available"] and result["market_data"]["reason"] == "INVALID_RESPONSE"


def test_alpha_retry_refreshes_only_alpha_and_preserves_spot_cache_date(rig, monkeypatch):
    import main
    clock, _, remote = rig
    monkeypatch.setattr(main, "get_market_candidates", lambda **kwargs: [])
    monkeypatch.setattr(market_cache.time, "time", clock)
    spot = {"symbol": "BTCUSDC", "market_source": "BINANCE_SPOT", "quantity": 1,
            "is_active": 1, "status": "MANUAL", "source": "MANUAL"}
    monkeypatch.setattr(main, "list_positions", lambda enrich=True: [spot])
    scans = []
    def scan(**kwargs):
        scans.append(kwargs["watchlist"])
        return [{"symbol": "BTCUSDC", "error": "simulated analysis"}]
    monkeypatch.setattr(main.scanner, "scan_watchlist_multi", scan)
    personal_data.create_watch_candidate({"symbol": "TOK0"})
    first = main.api_market(force=True)
    before_spot = next(row for row in first["results"] if row["symbol"] == "BTCUSDC")
    clock.advance(601)
    remote.failures["ticker"] = requests.Timeout()
    second = main.api_market()
    assert len(scans) == 1
    assert second["updated_at"] == first["updated_at"]
    assert next(row for row in second["results"] if row["symbol"] == "BTCUSDC") == before_spot
    assert next(row for row in second["results"] if row["symbol"] == "TOK0")["market_data"]["state"] == "STALE"
    assert "alpha_refresh_required" not in second


def test_alpha_refresh_cannot_resurrect_invalidated_or_replace_newer_cache(tmp_path, monkeypatch):
    path = tmp_path/"cache.db"
    payload = market_cache.save_market_payload({"results": []}, path)
    market_cache.clear_market_payload_cache(path)
    assert market_cache.save_alpha_refresh(payload, path) is None
    new = market_cache.save_market_payload({"results": [{"symbol": "BTCUSDC"}]}, path)
    assert market_cache.save_alpha_refresh(payload, path) is None
    assert market_cache.get_market_payload_cache(path)["results"] == new["results"]

    # Invalidate after the SELECT but before the conditional write. A preliminary
    # existence check alone cannot prevent resurrection by INSERT OR REPLACE.
    connect = market_cache._connect
    class ConcurrentInvalidation:
        def __init__(self):
            self.connection = connect(path)
        def __enter__(self):
            self.connection.__enter__()
            return self
        def __exit__(self, *args):
            return self.connection.__exit__(*args)
        def execute(self, sql, parameters=()):
            if sql.startswith(("UPDATE market_cache", "INSERT OR REPLACE")):
                with connect(path) as other:
                    market_cache.invalidate_market_cache(other)
            return self.connection.execute(sql, parameters)
    monkeypatch.setattr(market_cache, "_connect", lambda _: ConcurrentInvalidation())
    changed = {**new, "results": [{"symbol": "TOK0"}]}
    assert market_cache.save_alpha_refresh(changed, path) is None


def test_current_cockpit_quote_does_not_relabel_dated_accounting_as_current(rig):
    import main
    record = {"symbol": "TOK0", "market_source": "BINANCE_ALPHA",
              "current_price": 1, "global_score": 55,
              "market_data": {"state": "FRESH", "last_known_price": 3.25,
                              "last_known_price_quote_asset": "USDT"}}
    result = main.build_alpha_market_row({"symbol": "TOK0", "position_info": record})
    assert result["current_price"] == 3.25 and result["price_quote_asset"] == "USDT"
    assert record["current_price"] == 1


def test_server_stale_quote_retries_after_cooldown_instead_of_caching_for_ten_minutes(rig):
    clock, _, remote = rig
    remote.failures["ticker"] = Response({"lastPrice": "1", "closeTime": (clock()-601)*1000})
    old = alpha.fetch_alpha_ticker("TOK0")
    assert old["market_data"]["state"] == "STALE"
    before = len(remote.calls)
    remote.failures.clear()
    assert alpha.fetch_alpha_ticker("TOK0")["current_price"] is None
    assert len(remote.calls) == before
    clock.advance(6)
    assert alpha.fetch_alpha_ticker("TOK0")["current_price"] == 3.25
    assert len(remote.calls) == before+1


def test_recent_response_with_old_quote_is_only_dated_metadata(rig):
    clock, _, remote = rig
    remote.failures["ticker"] = Response({"lastPrice": "1", "closeTime": (clock()-2401)*1000})
    result = alpha.fetch_alpha_ticker("TOK0")
    assert result["current_price"] is None and result["market_data"]["state"] == "STALE"
    assert result["market_data"].get("last_known_price") == 1
    assert result["market_data"]["reason"] == "STALE_DATA"


def test_older_server_quote_cannot_replace_newer_last_known_snapshot(rig):
    clock, _, remote = rig
    alpha.fetch_alpha_ticker("TOK0")
    clock.advance(601)
    remote.failures["ticker"] = Response({"lastPrice": "1", "closeTime": (clock()-1200)*1000})
    result = alpha.fetch_alpha_ticker("TOK0")
    assert result["current_price"] is None
    assert result["market_data"]["last_known_price"] == 3.25


def test_alpha_market_cap_metadata_remains_available(rig):
    assert alpha.get_alpha_market_data("TOK0")["market_cap"] == 1000000

def test_unaddressable_catalog_symbols_do_not_disable_supported_alpha_tokens(rig):
    _, _, remote = rig
    remote.failures["catalog"] = Response([
        {"symbol": "TOK0", "alphaId": "ALPHA_0"},
        {"symbol": "\u5e01\u5b89\u4eba\u751f", "alphaId": "ALPHA_1"},
        {"symbol": "OTHER.COIN", "alphaId": "ALPHA_2"}])
    assert alpha.get_alpha_market_data("TOK0")["price_available"] is True
    assert symbols.resolve_market_symbol("MISSING")["valid"] is False
