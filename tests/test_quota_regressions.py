"""Public-only cockpit and shared-IP quota regressions, with controlled clocks."""
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
import json
import sqlite3
import threading
import time
from urllib.parse import urlsplit

import pytest
import requests

from services import binance_api_budget as quotas, symbols
from services.binance_private_client import BinancePrivateClient, BinanceReadError
from services.binance_sync_planner import load_catalog
from services.history import DB_PATH, init_db
from test_financial_transport import Response

BASE = "https://api.binance.com"
TRADE = BASE + "/api/v3/myTrades"
CATALOG = BASE + "/api/v3/exchangeInfo"


def limits(weight=6000, raw=61000):
    return [{"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "intervalNum": 1, "limit": weight},
            {"rateLimitType": "RAW_REQUESTS", "interval": "MINUTE", "intervalNum": 5, "limit": raw},
            {"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 1}]


class Clock:
    def __init__(self, value=100):
        self.value = value

    def __call__(self):
        return self.value


class PublicBinance:
    """No network; independent literal endpoint costs and remote fixed windows."""
    def __init__(self, clock, weight=6000, headers=True):
        self.clock, self.weight, self.headers = clock, weight, headers
        self.lock = threading.Lock()
        self.calls = []
        self.minute = {}
        self.catalog = [{"symbol": f"AS{i:02d}USDC", "baseAsset": f"AS{i:02d}",
                         "quoteAsset": "USDC", "status": "TRADING"} for i in range(26)]
        self.catalog.append({"symbol": "BTCUSDC", "baseAsset": "BTC", "quoteAsset": "USDC", "status": "TRADING"})

    def get(self, url, **kwargs):
        path = urlsplit(url).path
        params = kwargs.get("params") or {}
        costs = {"/api/v3/exchangeInfo": 20, "/api/v3/klines": 2,
                 "/api/v3/account": 20, "/api/v3/myTrades": 20}
        cost = costs.get(path, 2 if params.get("symbol") else 80)
        with self.lock:
            bucket = int(self.clock() // 60)
            self.minute[bucket] = self.minute.get(bucket, 0) + cost
            used = self.minute[bucket]
            self.calls.append({"path": path, "weight": cost, "symbol": params.get("symbol"),
                               "signed": "signature" in params})
        headers = {"X-MBX-USED-WEIGHT-1M": str(used), "Date": formatdate(self.clock(), usegmt=True)} if self.headers else {}
        if path == "/api/v3/exchangeInfo":
            data = {"symbols": self.catalog, "rateLimits": limits(self.weight)}
        elif path == "/api/v3/klines":
            data = [[i*60000, str(100+i), str(102+i), str(99+i), str(101+i),
                     str(1000+i), (i+1)*60000-1, "100000", 100,
                     str(600+i), "60000", "0"] for i in range(params.get("limit", 120))]
        elif path == "/api/v3/ticker/24hr":
            data = []
        elif path == "/api/v3/ticker/price":
            data = {"symbol": params.get("symbol"), "price": "100"}
        elif path == "/api/v3/account":
            data = {"balances": []}
        elif path == "/api/v3/myTrades":
            data = []
        else:
            raise AssertionError("Unexpected fake endpoint")
        return Response(data=data, headers=headers)


def install_public(monkeypatch, path=DB_PATH, weight=6000, headers=True):
    clock = Clock()
    budget = quotas.ApiBudget(path, clock=clock)
    fake = PublicBinance(clock, weight, headers)
    monkeypatch.setattr(quotas, "ApiBudget", lambda: budget)
    monkeypatch.setattr(requests, "get", fake.get)
    monkeypatch.setattr(symbols, "_symbol_cache", None)
    monkeypatch.setattr(symbols, "_cache_loaded_at", 0)
    return budget, fake, clock


def seed_cockpit(path=DB_PATH, positions=20, watches=6):
    init_db(path)
    with sqlite3.connect(path) as connection:
        for i in range(positions):
            connection.execute("""INSERT INTO positions
                (symbol,quantity,invested_amount,source,status,is_active,market_source,created_at,updated_at)
                VALUES(?,1,100,'MANUAL','MANUAL',1,'BINANCE_SPOT','synthetic','synthetic')""", (f"AS{i:02d}USDC",))
        for i in range(positions, positions+watches):
            connection.execute("""INSERT INTO watch_candidates
                (symbol,priority,market_source,created_at,updated_at)
                VALUES(?,'MEDIUM','BINANCE_SPOT','synthetic','synthetic')""", (f"AS{i:02d}USDC",))


def private(budget):
    config = type("Config", (), {"api_key": "SYNTHETIC", "api_secret": "SYNTHETIC",
                                "use_testnet": False, "configured": True})()
    return BinancePrivateClient(config, budget=budget, sleep=lambda delay: None)


@pytest.mark.parametrize("official", [6000, 100])
def test_first_public_catalog_without_keys_publishes_actual_limit(tmp_path, monkeypatch, official):
    budget, fake, clock = install_public(monkeypatch, tmp_path/"db.sqlite", official)
    assert not __import__("os").environ.get("BINANCE_API_KEY")
    assert "AS00USDC" in symbols.get_exchange_symbols()
    current = budget.metrics()
    assert current["published"]
    assert current["limits"] == [
        {"metric": "REQUEST_WEIGHT", "seconds": 60, "limit": official, "source": "EXCHANGE_INFO"},
        {"metric": "RAW_REQUESTS", "seconds": 300, "limit": 61000, "source": "EXCHANGE_INFO"}]
    assert current["windows"]["REQUEST_WEIGHT:60"]["used"] == 20
    assert current["requests"] == 1
    assert not fake.calls[0]["signed"]
    assert symbols.get_exchange_symbols()  # Cache never fetches merely to seed again.
    assert len(fake.calls) == 1
    if official == 100:
        for _ in range(3):
            quotas.budgeted_get(TRADE, _budget=budget)
        with pytest.raises(quotas.ApiQuotaError):
            quotas.budgeted_get(TRADE, _budget=budget)
        assert len(fake.calls) == 4


@pytest.mark.parametrize("headers", [True, False])
def test_20_positions_6_watches_complete_without_binance_keys(monkeypatch, record_property, headers):
    import main
    budget, fake, clock = install_public(monkeypatch, headers=headers)
    seed_cockpit()
    started = time.perf_counter()
    result = main.api_market(force=True)
    elapsed = time.perf_counter()-started
    assert result["counts"]["positions"] == 20
    assert result["counts"]["watch_candidates"] == 6
    assert len(result["results"]) == 26
    assert all("error" not in row and row.get("timeframes") for row in result["results"])
    metric = budget.metrics()
    assert 480 < metric["weight"] < 4800
    assert metric["weight"] == sum(call["weight"] for call in fake.calls)
    assert metric["windows"]["REQUEST_WEIGHT:60"]["used"] == metric["weight"]
    assert not any(call["signed"] for call in fake.calls)
    assert len([call for call in fake.calls if call["path"].endswith("exchangeInfo")]) == 1
    before = len(fake.calls)
    assert main.api_market()["results"] == result["results"]
    assert len(fake.calls) == before  # Cached cockpit remains usable even at quota.
    second = main.api_market(force=True)
    assert len(second["results"]) == 26
    assert all("error" not in row for row in second["results"])
    record_property("quota_cockpit", json.dumps({"calls_first": before, "weight_first": metric["weight"],
        "limit": 6000, "effective": 4800, "success": 26, "refused": 0,
        "quota_wait_seconds": 0, "local_seconds": elapsed, "headers": headers,
        "calls_after_second": len(fake.calls), "weight_after_second": budget.metrics()["weight"]}))


@pytest.mark.parametrize("btc_in_universe", [True, False])
def test_real_low_limit_refuses_before_transport_and_preserves_positions(monkeypatch, btc_in_universe):
    import main
    from services.personal_data import list_positions
    budget, fake, clock = install_public(monkeypatch, weight=600)
    seed_cockpit()
    # Include BTC in this lower-capacity universe so market-context reads use
    # its scanned row, just as a normal BTC holding would. No decision stub.
    if btc_in_universe:
        with sqlite3.connect(DB_PATH) as connection:
            connection.execute("UPDATE positions SET symbol='BTCUSDC' WHERE symbol='AS00USDC'")
    original = list_positions(enrich=False)
    result = main.api_market(force=True)
    assert any("error" in row or not row.get("timeframes") for row in result["results"])
    assert all("decision_engine" not in row for row in result["results"] if "error" in row)
    assert budget.metrics()["weight"] <= 480
    assert list_positions(enrich=False) == original
    calls = len(fake.calls)
    cached = main.api_market()
    assert cached["results"] == result["results"]
    assert len(fake.calls) == calls


@pytest.mark.parametrize("first", ["public", "private"])
def test_public_private_order_shares_limits_counts_and_no_extra_catalog(tmp_path, monkeypatch, first):
    budget, fake, clock = install_public(monkeypatch, tmp_path/"db.sqlite")
    client = private(budget)
    if first == "public":
        symbols.get_exchange_symbols()
        client.get_my_trades("AS00USDC")
    else:
        client.get_my_trades("AS00USDC")
        symbols.get_exchange_symbols()
    assert budget.metrics()["weight"] == 40
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:60"]["used"] == 40
    assert len(fake.calls) == 2


@pytest.mark.parametrize("payload", [
    None, {}, [], [{"rateLimitType": []}], [{"rateLimitType": {}}], [{"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 1, "limit": 1}],
    [{"rateLimitType": "RAW_REQUESTS", "interval": "MINUTE", "intervalNum": 1, "limit": 1}],
    limits() + [{"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "intervalNum": [], "limit": 1}],
    limits() + [{"rateLimitType": "REQUEST_WEIGHT", "interval": [], "intervalNum": 1, "limit": 1}],
])
def test_missing_or_invalid_metadata_cannot_destroy_official_limits(tmp_path, payload):
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=Clock())
    assert budget.publish_limits("SPOT", limits(1000))
    assert not budget.publish_limits("SPOT", payload)
    assert budget.metrics()["limits"][0]["limit"] == 1000


def test_default_and_configured_fallbacks_are_network_specific(tmp_path, monkeypatch):
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=Clock())
    assert budget.metrics()["limits"][0]["limit"] == 6000
    assert budget.metrics("ALPHA")["limits"][0]["limit"] == 600
    assert budget.metrics("SPOT_TESTNET")["limits"][0]["limit"] == 600
    monkeypatch.setenv("CRYPTOAI_API_SPOT_WEIGHT_PER_MINUTE", "1000")
    monkeypatch.setenv("CRYPTOAI_API_SPOT_RAW_REQUESTS", "5000")
    assert budget.metrics()["limits"][0]["limit"] == 1000
    assert budget.metrics()["limits"][1]["limit"] == 5000
    budget.publish_limits("SPOT", limits(6000))
    assert budget.metrics()["limits"][0]["limit"] == 6000
    monkeypatch.setenv("CRYPTOAI_API_SPOT_WEIGHT_PER_MINUTE", "10")
    assert budget.metrics()["limits"][0]["limit"] == 6000


@pytest.mark.parametrize("value", ["", "-1", "0", "NaN", "1.5", "1000000001"])
def test_invalid_fallback_configuration_fails_closed(tmp_path, monkeypatch, value):
    monkeypatch.setenv("CRYPTOAI_API_SPOT_WEIGHT_PER_MINUTE", value)
    with pytest.raises(ValueError, match="Invalid API fallback setting"):
        quotas.ApiBudget(tmp_path/"db.sqlite").metrics()


def test_cached_or_out_of_order_catalog_never_overwrites_newer_limits(tmp_path):
    clock = Clock()
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=clock)
    assert budget.publish_limits("SPOT", limits(6000), observed_at=90, from_cache=True)
    assert budget.publish_limits("SPOT", limits(1000), observed_at=100)
    assert not budget.publish_limits("SPOT", limits(6000), observed_at=99)
    clock.value = 101
    assert not budget.publish_limits("SPOT", limits(6000), observed_at=101, from_cache=True)
    assert budget.metrics()["limits"][0]["limit"] == 1000
    assert budget.publish_limits("SPOT", limits(500))  # Same-clock fresh observations remain valid.
    assert budget.metrics()["limits"][0]["limit"] == 500


def test_cached_planner_catalog_cannot_restore_an_obsolete_higher_limit(tmp_path, monkeypatch):
    budget, fake, clock = install_public(monkeypatch, tmp_path/"db.sqlite")
    client = private(budget)
    rows, fresh, fetched = load_catalog(client, budget.path, "BINANCE_SPOT", clock=clock)
    assert fetched and rows
    clock.value = 101
    budget.publish_limits("SPOT", limits(1000))
    rows, fresh, fetched = load_catalog(client, budget.path, "BINANCE_SPOT", clock=clock)
    assert not fetched and fresh
    assert budget.metrics()["limits"][0]["limit"] == 1000
    assert len(fake.calls) == 1


def test_missing_raw_metadata_preserves_published_raw_policy(tmp_path):
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=Clock())
    budget.publish_limits("SPOT", limits(6000, raw=5))
    budget.publish_limits("SPOT", limits(1000)[:1])
    assert next(x for x in budget.metrics()["limits"] if x["metric"] == "RAW_REQUESTS")["limit"] == 5
    for _ in range(4):
        network, token = budget.reserve(BASE+"/api/v3/klines")
        budget.observe(network, Response(), token)
    with pytest.raises(quotas.ApiQuotaError):
        budget.reserve(BASE+"/api/v3/klines")


@pytest.mark.parametrize("headers", [
    {"X-MBX-USED-WEIGHT-1M": "4700", "x-mbx-used-weight-1m": "100"},
    {"x-mbx-used-weight-1m": "100", "X-MBX-USED-WEIGHT-1M": "4700"},
    {"X-MBX-USED-WEIGHT-1M": "4700", "x-mbx-used-weight-1m": "invalid"},
])
def test_duplicate_or_conflicting_headers_use_greatest_valid_usage(tmp_path, headers):
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=Clock())
    net, token = budget.reserve(TRADE)
    budget.observe(net, Response(headers=headers), token)
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:60"]["used"] == 4700
    for _ in range(5):
        net, token = budget.reserve(TRADE)
        budget.observe(net, Response(), token)
    with pytest.raises(quotas.ApiQuotaError):
        budget.reserve(TRADE)


def test_unknown_header_interval_observes_consumption_without_inventing_limit(tmp_path):
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=Clock())
    net, token = budget.reserve(TRADE)
    budget.observe(net, Response(headers={"X-MBX-USED-WEIGHT-1H": "8000"}), token)
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:3600"]["observed"] == 8000
    assert len(budget.metrics()["limits"]) == 2
    budget.reserve(TRADE)


def test_completed_parallel_headers_retire_estimates_without_lowering_remote_observation(tmp_path):
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=Clock())
    reservations = [budget.reserve(TRADE) for _ in range(50)]
    for net, token in reservations:
        budget.observe(net, Response(headers={"X-MBX-USED-WEIGHT-1M": "1000"}), token)
    window = budget.metrics()["windows"]["REQUEST_WEIGHT:60"]
    assert window["observed"] == window["used"] == window["local"] == 1000
    assert window["unconfirmed"] == 0
    net, token = budget.reserve(TRADE)
    budget.observe(net, Response(), token)
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:60"]["used"] == 1020


def test_old_window_header_does_not_saturate_new_minute(tmp_path):
    clock = Clock(119.9)
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=clock)
    net, old = budget.reserve(TRADE)
    clock.value = 120.1
    budget.observe(net, Response(headers={"Date": formatdate(119, usegmt=True),
        "X-MBX-USED-WEIGHT-1M": "4799"}), old)
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:60"]["used"] == 0
    net, token = budget.reserve(TRADE)
    budget.observe(net, Response(headers={"Date": formatdate(120, usegmt=True),
        "X-MBX-USED-WEIGHT-1M": "20"}), token)
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:60"]["used"] == 20


def test_inflight_request_is_carried_over_boundary_before_another_reservation(tmp_path):
    clock = Clock(119.9)
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=clock)
    budget.publish_limits("SPOT", limits(25))  # Exactly one 20-weight request with margin.
    net, old = budget.reserve(TRADE)
    clock.value = 120.1
    with pytest.raises(quotas.ApiQuotaError):
        budget.reserve(TRADE)
    budget.observe(net, Response(headers={"Date": formatdate(120, usegmt=True),
        "X-MBX-USED-WEIGHT-1M": "20"}), old)
    with pytest.raises(quotas.ApiQuotaError):
        budget.reserve(TRADE)
    clock.value = 180.1
    budget.reserve(TRADE)


def test_existing_policy_upgrade_keeps_ban_and_usage_removes_legacy_ceiling(tmp_path):
    path = tmp_path/"db.sqlite"
    budget = quotas.ApiBudget(path, clock=Clock())
    net, token = budget.reserve(TRADE)
    budget.observe(net, Response(), token)
    with sqlite3.connect(path) as connection:
        state = json.loads(connection.execute("SELECT payload_json FROM binance_api_state").fetchone()[0])
        state.pop("policy_version")
        state["limits"][0].update(limit=600, source="CONSERVATIVE_FALLBACK")
        state["limits"].append({"metric": "REQUEST_WEIGHT", "seconds": 3600,
            "limit": 600, "source": "HEADER_WITH_UNKNOWN_LIMIT"})
        state["blocked_until"], state["block_reason"] = 200, "HTTP_418"
        connection.execute("UPDATE binance_api_state SET payload_json=?", (json.dumps(state),))
    assert budget.metrics()["limits"][0]["limit"] == 6000
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:60"]["used"] == 20
    with pytest.raises(quotas.ApiQuotaError) as error:
        budget.reserve(TRADE)
    assert error.value.code == "HTTP_418"


@pytest.mark.parametrize("status", [429, 418])
def test_remote_suspension_wins_even_when_local_capacity_remains(tmp_path, monkeypatch, status):
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=Clock())
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(k) or Response(status, headers={"Retry-After": "120"}))
    for _ in range(2):
        with pytest.raises(quotas.ApiQuotaError) as error:
            quotas.budgeted_get(CATALOG, _budget=budget)
        assert error.value.retry_after == 120
    assert len(calls) == 1
    assert budget.metrics()["weight"] == 20


def test_atomic_cross_client_competition_for_true_last_capacity(tmp_path):
    path = tmp_path/"db.sqlite"
    clock = Clock()
    budget = quotas.ApiBudget(path, clock=clock)
    budget.publish_limits("SPOT", limits(1000))
    def send(i):
        instance = quotas.ApiBudget(path, clock=clock)
        url = TRADE if i % 2 else BASE+"/api/v3/klines"
        try:
            net, token = instance.reserve(url)
            instance.observe(net, Response(), token)
            return 20 if i % 2 else 2
        except quotas.ApiQuotaError:
            return 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = list(pool.map(send, range(200)))
    assert 780 <= sum(accepted) <= 800
    assert budget.metrics()["weight"] == sum(accepted)
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:60"]["used"] == sum(accepted)
    assert accepted.count(0) > 0


def test_cockpit_parallel_with_incremental_sync_and_positions(monkeypatch, tmp_path, record_property):
    import main
    from services import binance_sync
    from services.personal_data import list_positions
    from services.binance_sync_jobs import SyncJobs
    budget, fake, clock = install_public(monkeypatch)
    seed_cockpit()
    # Seed three synthetic historical markets without HTTP; the incremental
    # worker must actually paginate them while the public cockpit is running.
    from test_financial_reference import PagedClient, trade
    from services.binance_trade_history import sync_market_history
    historical = {f"AS{i:02d}USDC": [trade(symbol=f"AS{i:02d}USDC")] for i in range(3)}
    for symbol in historical:
        sync_market_history(symbol, PagedClient(historical), DB_PATH)
    # Use a real worker/orchestrator and the real transport on the same temp DB.
    jobs = SyncJobs(DB_PATH, client_factory=lambda: private(budget))
    try:
        job = jobs.start()["job"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            market = pool.submit(main.api_market, True)
            positions = pool.submit(list_positions)
            result = market.result(20)
            assert len([row for row in positions.result(20) if not row.get("synced_from_binance")]) == 20
        thread = jobs._threads.get(job["id"])
        if thread:
            thread.join(10)
        state = jobs.get(job["id"])
        assert state["status"] in {"COMPLETED", "PARTIAL"}
        assert state["processed"] == state["total"] == 3
        assert state["pages"] >= 3
        assert len(result["results"]) == 26
        assert all("error" not in row and row.get("timeframes") for row in result["results"])
        metric = budget.metrics()
        assert metric["weight"] == sum(c["weight"] for c in fake.calls)
        assert metric["windows"]["REQUEST_WEIGHT:60"]["used"] == metric["weight"]
        assert any(c["path"] == "/api/v3/account" for c in fake.calls)
        assert len([c for c in fake.calls if c["path"] == "/api/v3/myTrades"]) == 3
        assert metric["weight"] < 4800
        record_property("quota_concurrent", json.dumps({"calls": len(fake.calls), "weight": metric["weight"],
            "success": 26, "refused": 0, "sync_status": state["status"], "quota_wait_seconds": 0}))
    finally:
        jobs.shutdown()


def test_redirect_cannot_issue_unreserved_second_request(tmp_path, monkeypatch):
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=Clock())
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(k) or Response(302))
    with pytest.raises(requests.RequestException, match="redirect"):
        quotas.budgeted_get(TRADE, _budget=budget)
    assert len(calls) == 1 and calls[0]["allow_redirects"] is False
    assert budget.metrics()["weight"] == 20


def test_wallet_snapshot_has_independent_endpoint_budget_and_headers(tmp_path):
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=Clock())
    net, token = budget.reserve(BASE+"/sapi/v1/accountSnapshot")
    assert net == "SPOT_SNAPSHOT"
    assert next(x for x in budget.metrics(net)["limits"] if x["metric"] == "RAW_REQUESTS")["source"] == "CONSERVATIVE_FALLBACK"
    budget.observe(net, Response(headers={"X-SAPI-USED-IP-WEIGHT-1M": "9000"}), token)
    assert budget.metrics()["weight"] == 0
    assert budget.metrics(net)["windows"]["REQUEST_WEIGHT:60"]["used"] == 9000
    with pytest.raises(quotas.ApiQuotaError):
        budget.reserve(BASE+"/sapi/v1/accountSnapshot")
    budget.reserve(TRADE)


@pytest.mark.parametrize("size,expected", [(1, 2), (20, 2), (21, 40), (100, 40), (101, 80)])
def test_batch_ticker_weights_follow_documented_thresholds(size, expected):
    assert quotas.endpoint_weight("/api/v3/ticker/24hr", {"symbols": json.dumps(["BTCUSDC"]*size)}) == expected


def test_parallel_cold_public_catalog_fetch_is_shared(tmp_path, monkeypatch):
    budget, fake, clock = install_public(monkeypatch, tmp_path/"db.sqlite")
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: symbols.get_exchange_symbols(), range(36)))
    assert all(result == results[0] for result in results)
    assert len(fake.calls) == 1
    assert budget.metrics()["weight"] == 20


@pytest.mark.parametrize("metadata", [None, [], [{"rateLimitType": "ORDERS", "limit": 1}],
    [{"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "intervalNum": 1, "limit": "6000"}]])
def test_first_public_catalog_missing_metadata_uses_spot_fallback(tmp_path, monkeypatch, metadata):
    budget, fake, clock = install_public(monkeypatch, tmp_path/"db.sqlite")
    def get(url, **kwargs):
        response = fake.get(url, **kwargs)
        response.data["rateLimits"] = metadata
        return response
    monkeypatch.setattr(requests, "get", get)
    assert symbols.get_exchange_symbols()
    assert not budget.metrics()["published"]
    assert budget.metrics()["limits"][0]["limit"] == 6000
    assert budget.metrics()["weight"] == 20


def test_config_change_replaces_durable_fallback_but_never_official_policy(tmp_path, monkeypatch):
    path = tmp_path/"db.sqlite"
    clock = Clock()
    budget = quotas.ApiBudget(path, clock=clock)
    net, token = budget.reserve(TRADE)
    budget.observe(net, Response(), token)
    monkeypatch.setenv("CRYPTOAI_API_SPOT_WEIGHT_PER_MINUTE", "1000")
    reopened = quotas.ApiBudget(path, clock=clock)
    assert reopened.metrics()["limits"][0]["limit"] == 1000
    assert reopened.metrics()["weight"] == 20
    assert reopened.publish_limits("SPOT", limits(500))
    monkeypatch.setenv("CRYPTOAI_API_SPOT_WEIGHT_PER_MINUTE", "6000")
    assert reopened.metrics()["limits"][0]["limit"] == 500


def test_same_tick_conflicting_metadata_cannot_relax_official_policy(tmp_path):
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=Clock())
    assert budget.publish_limits("SPOT", limits(1000), observed_at=100)
    assert budget.publish_limits("SPOT", limits(6000), observed_at=100)
    assert budget.metrics()["limits"][0]["limit"] == 1000


@pytest.mark.parametrize("status", [429, 418])
def test_retry_after_http_date_and_expiration(tmp_path, status):
    clock = Clock()
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=clock)
    net, token = budget.reserve(TRADE)
    with pytest.raises(quotas.ApiQuotaError) as error:
        budget.observe(net, Response(status, headers={"retry-after": formatdate(160, usegmt=True)}), token)
    assert error.value.retry_after == 60
    clock.value = 159.9
    with pytest.raises(quotas.ApiQuotaError):
        budget.reserve(TRADE)
    clock.value = 160
    budget.reserve(TRADE)


def test_header_usage_is_shared_across_hosts_and_survives_restart(tmp_path):
    path = tmp_path/"db.sqlite"
    clock = Clock()
    budget = quotas.ApiBudget(path, clock=clock)
    net, token = budget.reserve(BASE+"/api/v3/klines")
    budget.observe(net, Response(headers={"X-MBX-USED-WEIGHT-1M": "4799"}), token)
    for host in ["api1.binance.com", "api-gcp.binance.com", "data-api.binance.vision"]:
        with pytest.raises(quotas.ApiQuotaError):
            quotas.ApiBudget(path, clock=clock).reserve("https://"+host+"/api/v3/klines")
    clock.value = 120
    quotas.ApiBudget(path, clock=clock).reserve(TRADE)


def test_spot_metadata_and_headers_cannot_initialize_alpha_or_testnet(tmp_path):
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=Clock())
    assert not budget.publish_limits("ALPHA", limits())
    net, token = budget.reserve("https://www.binance.com/bapi/alpha")
    budget.observe(net, Response(headers={"X-MBX-USED-WEIGHT-1M": "6000"}), token)
    assert budget.metrics("ALPHA")["windows"]["REQUEST_WEIGHT:60"]["used"] == 80
    budget.publish_limits("SPOT", limits(1000))
    assert budget.metrics("SPOT_TESTNET")["limits"][0]["limit"] == 600


def test_preview_uses_effective_known_limit_and_interval(tmp_path, monkeypatch):
    from services.binance_sync_planner import preview
    budget, fake, clock = install_public(monkeypatch, tmp_path/"db.sqlite", weight=1000)
    client = private(budget)
    result = preview(client, budget.path, "BINANCE_SPOT", symbols=["AS00USDC"], clock=clock)
    assert result["cost"]["weight_limit_assumed"] == 1000
    assert result["cost"]["safety"] == .8
    assert result["cost"]["weight_interval_seconds"] == 60
    clock.value += 1
    budget.publish_limits("SPOT", [{"rateLimitType": "REQUEST_WEIGHT", "interval": "SECOND",
        "intervalNum": 10, "limit": 100}])
    result = preview(client, budget.path, "BINANCE_SPOT", symbols=["AS00USDC"], clock=clock)
    assert result["cost"]["weight_limit_assumed"] == 100
    assert result["cost"]["weight_interval_seconds"] == 10


def _quota_process_compete(path):
    budget = quotas.ApiBudget(path, clock=Clock())
    for _ in range(20):
        try:
            budget.reserve(TRADE)
        except quotas.ApiQuotaError:
            pass


def test_reservation_lock_is_shared_across_real_processes(tmp_path):
    import multiprocessing
    path = tmp_path/"db.sqlite"
    budget = quotas.ApiBudget(path, clock=Clock())
    budget.publish_limits("SPOT", limits(1000))
    context = multiprocessing.get_context("fork")
    workers = [context.Process(target=_quota_process_compete, args=(path,)) for _ in range(4)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            assert worker.exitcode == 0
        assert budget.metrics()["requests"] == 40
        assert budget.metrics()["weight"] == 800
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)


def test_full_dashboard_20_positions_6_watches_two_refreshes(monkeypatch, record_property):
    """Real refreshAll order: personal panels, market, then cached AI alerts."""
    import main
    from auth_client import AuthenticatedClient
    budget, fake, clock = install_public(monkeypatch)
    seed_cockpit()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("UPDATE positions SET symbol='BTCUSDC' WHERE symbol='AS00USDC'")
    panels = ["/api/positions", "/api/watch-candidates", "/api/journal",
              "/api/journal/analytics", "/api/binance/pnl", "/api/binance/status"]
    measurements = []
    with AuthenticatedClient(main.app) as client:
        for _ in range(2):
            started = time.perf_counter()
            # Requests use all actual routes with active auth/session protections.
            # Deterministic order reproduces the same total work as Promise.all.
            for route in panels:
                assert client.get(route).status_code == 200
            response = client.get("/api/market?force=true")
            assert response.status_code == 200
            result = response.json()
            assert result["counts"]["positions"] == 20
            assert result["counts"]["watch_candidates"] == 6
            assert len(result["results"]) == 26
            assert all("error" not in row and row.get("timeframes") for row in result["results"])
            calls = len(fake.calls)
            assert client.get("/api/ai-alerts").status_code == 200
            assert len(fake.calls) == calls
            measurements.append({"calls_total": calls, "weight_total": budget.metrics()["weight"],
                                 "success": 26, "refused": 0, "local_seconds": time.perf_counter()-started})
        assert budget.metrics()["weight"] < 4800
        # Another application can saturate the shared IP after our successful
        # refresh. The valid cached market and alerts still require no HTTP.
        budget.observe("SPOT", Response(headers={"X-MBX-USED-WEIGHT-1M": "4800"}))
        calls = len(fake.calls)
        for route in ["/api/market", "/api/ai-alerts"]:
            assert client.get(route).status_code == 200
        assert len(fake.calls) == calls
    assert not any(call["signed"] for call in fake.calls)
    record_property("quota_full_dashboard", json.dumps(measurements))



@pytest.mark.parametrize("metric", ["REQUEST_WEIGHT", "RAW_REQUESTS"])
def test_new_shorter_official_interval_preserves_known_consumption(tmp_path, metric):
    clock = Clock()
    budget = quotas.ApiBudget(tmp_path/"db.sqlite", clock=clock)
    budget.publish_limits("SPOT", limits(1000))
    for _ in range(10):
        net, token = budget.reserve(TRADE)
        budget.observe(net, Response(), token)
    clock.value = 101
    updated = limits(1000)
    updated.append({"rateLimitType": metric, "interval": "SECOND", "intervalNum": 10,
                    "limit": 100 if metric == "REQUEST_WEIGHT" else 10})
    assert budget.publish_limits("SPOT", updated)
    with pytest.raises(quotas.ApiQuotaError):
        budget.reserve(TRADE)
    clock.value = 110
    budget.reserve(TRADE)
