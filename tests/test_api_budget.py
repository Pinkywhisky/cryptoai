"""Independent request-weight arithmetic and shared quota failure injection."""
from concurrent.futures import ThreadPoolExecutor
import math
import pytest
import requests
from services.binance_api_budget import ApiBudget, ApiQuotaError, budgeted_get, endpoint_weight, network_for
from test_financial_transport import Response

URL="https://api.binance.com/api/v3/myTrades"
LIMITS=[{"rateLimitType":"REQUEST_WEIGHT","interval":"MINUTE","intervalNum":1,"limit":6000},
        {"rateLimitType":"RAW_REQUESTS","interval":"MINUTE","intervalNum":5,"limit":61000}]


@pytest.mark.parametrize("path,params,weight",[
    ("/api/v3/account",{},20),("/api/v3/myTrades",{},20),
    ("/api/v3/myTrades",{"orderId":1},5),("/api/v3/exchangeInfo",{},20),
    ("/api/v3/allOrders",{},20),("/api/v3/klines",{},2),
    ("/api/v3/ticker/price",{"symbol":"BTCUSDC"},2),("/api/v3/ticker/price",{},4),
    ("/api/v3/ticker/24hr",{},80),("/api/v3/ticker/24hr",{"symbol":"BTCUSDC"},2),
    ("/sapi/v1/accountSnapshot",{},2400),("/bapi/unknown",{},80),
])
def test_documented_and_conservative_endpoint_weights(path,params,weight):
    assert endpoint_weight(path,params)==weight


@pytest.mark.parametrize("safety",[0,.09,.951,1,float("nan"),float("inf")])
def test_invalid_margin_rejected(tmp_path,safety):
    with pytest.raises(ValueError):
        ApiBudget(tmp_path/"db.sqlite",safety=safety)


def test_reservations_share_database_and_count_public_cockpit(tmp_path):
    path=tmp_path/"db.sqlite"
    first=ApiBudget(path,clock=lambda:100)
    second=ApiBudget(path,clock=lambda:100)
    first.reserve(URL)
    second.reserve("https://api.binance.com/api/v3/klines")
    second.reserve("https://api.binance.com/api/v3/ticker/24hr")
    metrics=ApiBudget(path,clock=lambda:100).metrics()
    assert metrics["requests"]==3
    assert metrics["weight"]==102  # 20 + 2 + 80, independent of implementation.
    assert metrics["windows"]["REQUEST_WEIGHT:60"]["used"]==102


def test_local_fallback_budget_never_overbooks_concurrent_clients(tmp_path,monkeypatch):
    monkeypatch.setenv("CRYPTOAI_API_SPOT_WEIGHT_PER_MINUTE","600")
    path=tmp_path/"db.sqlite"
    def reserve(_):
        try:
            ApiBudget(path,clock=lambda:100).reserve(URL)
            return True
        except ApiQuotaError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted=list(pool.map(reserve,range(40)))
    assert sum(accepted)==24  # floor(600 * .8) / weight 20.
    assert ApiBudget(path).metrics()["weight"]==480


def test_published_limits_and_simultaneous_headers_never_refund(tmp_path):
    budget=ApiBudget(tmp_path/"db.sqlite",clock=lambda:100)
    assert budget.publish_limits("SPOT",LIMITS)
    net,token1=budget.reserve(URL)
    _,token2=budget.reserve(URL)
    budget.observe(net,Response(headers={"X-MBX-USED-WEIGHT-1M":"1000"}),token1)
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:60"]["used"]==1020
    budget.observe(net,Response(headers={"x-mbx-used-weight-1m":"10"}),token2)
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:60"]["used"]==1000
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:60"]["observed"]==1000
    assert budget.metrics()["requests"]==2


@pytest.mark.parametrize("headers",[
    {},{"X-MBX-USED-WEIGHT-1M":"abc"},{"X-MBX-USED-WEIGHT-1M":"-1"},
    {"X-MBX-USED-WEIGHT-1M":"1.2"},{"X-MBX-USED-WEIGHT-999999999D":"20"},
])
def test_missing_or_malformed_headers_preserve_local_usage(tmp_path,headers):
    budget=ApiBudget(tmp_path/"db.sqlite",clock=lambda:100)
    network,token=budget.reserve(URL)
    budget.observe(network,Response(headers=headers),token)
    metric=budget.metrics()
    assert metric["windows"]["REQUEST_WEIGHT:60"]["used"]>=20
    assert metric["missing_headers"]+metric["invalid_headers"]>=1


@pytest.mark.parametrize("payload",[
    None,[],[{"rateLimitType":"REQUEST_WEIGHT","interval":"MINUTE","intervalNum":True,"limit":6000}],
    [{"rateLimitType":"REQUEST_WEIGHT","interval":"MINUTE","intervalNum":1,"limit":-1}],
    [{"rateLimitType":"REQUEST_WEIGHT","interval":"BAD","intervalNum":1,"limit":6000}],
    LIMITS+LIMITS,
])
def test_invalid_published_limits_keep_conservative_policy(tmp_path,payload):
    budget=ApiBudget(tmp_path/"db.sqlite")
    assert not budget.publish_limits("SPOT",payload)
    assert budget.metrics()["limits"][0]["limit"]==6000


@pytest.mark.parametrize("status,header,minimum",[
    (429,"120",120),(418,"3600",3600),(429,None,60),(418,None,259200),
    (429,"NaN",60),(418,"-1",259200),
])
def test_bans_persist_across_client_restart_and_apply_to_public_calls(tmp_path,status,header,minimum):
    clock=[100.]
    path=tmp_path/"db.sqlite"
    budget=ApiBudget(path,clock=lambda:clock[0])
    network,token=budget.reserve(URL)
    with pytest.raises(ApiQuotaError) as raised:
        budget.observe(network,Response(status,headers={} if header is None else {"Retry-After":header}),token)
    assert raised.value.retry_after>=minimum
    restarted=ApiBudget(path,clock=lambda:clock[0])
    with pytest.raises(ApiQuotaError):
        restarted.reserve("https://api.binance.com/api/v3/klines")
    clock[0]+=minimum+1
    restarted.reserve(URL)
    assert restarted.metrics()["block_reason"] is None


def test_no_retry_after_429_and_no_request_during_suspension(tmp_path,monkeypatch):
    budget=ApiBudget(tmp_path/"db.sqlite",clock=lambda:100)
    calls=[]
    monkeypatch.setattr(requests,"get",lambda *a,**k:calls.append(k) or Response(429,headers={"Retry-After":"120"}))
    for _ in range(2):
        with pytest.raises(ApiQuotaError):
            budgeted_get(URL,_budget=budget,params={"symbol":"ABCUSDC"})
    assert len(calls)==1


def test_transport_failure_keeps_reserved_weight(tmp_path,monkeypatch):
    budget=ApiBudget(tmp_path/"db.sqlite",clock=lambda:100)
    def fail(*a,**k):
        raise requests.Timeout("synthetic-private-url")
    monkeypatch.setattr(requests,"get",fail)
    with pytest.raises(requests.Timeout):
        budgeted_get(URL,_budget=budget)
    assert budget.metrics()["weight"]==20


def test_all_published_windows_apply_and_reset_at_boundaries(tmp_path):
    clock=[59.5]
    budget=ApiBudget(tmp_path/"db.sqlite",clock=lambda:clock[0])
    limits=[{"rateLimitType":"REQUEST_WEIGHT","interval":"MINUTE","intervalNum":1,"limit":100},
            {"rateLimitType":"REQUEST_WEIGHT","interval":"SECOND","intervalNum":10,"limit":50}]
    budget.publish_limits("SPOT",limits)
    for _ in range(2):
        network,token=budget.reserve(URL)
        budget.observe(network,Response(),token)
    with pytest.raises(ApiQuotaError) as info:
        budget.reserve(URL)
    assert info.value.retry_after==1
    clock[0]=60
    budget.reserve(URL)
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:10"]["used"]==20


def test_networks_remain_isolated_and_unknown_hosts_refused(tmp_path):
    budget=ApiBudget(tmp_path/"db.sqlite",clock=lambda:100)
    budget.reserve(URL)
    budget.reserve("https://testnet.binance.vision/api/v3/myTrades")
    assert budget.metrics("SPOT")["weight"]==20
    assert budget.metrics("SPOT_TESTNET")["weight"]==20
    assert network_for("https://www.binance.com/bapi/a")=="ALPHA"
    with pytest.raises(ValueError):
        budget.reserve("https://attacker.invalid/api/v3/myTrades")


def test_quota_exception_contains_no_request_credentials(tmp_path,monkeypatch):
    monkeypatch.setenv("CRYPTOAI_API_SPOT_WEIGHT_PER_MINUTE","600")
    budget=ApiBudget(tmp_path/"db.sqlite",clock=lambda:100)
    for _ in range(24):
        budget.reserve(URL,{"signature":"SYNTHETIC_SECRET"})
    with pytest.raises(ApiQuotaError) as info:
        budget.reserve(URL)
    assert "SYNTHETIC" not in str(info.value)
    assert "signature" not in str(budget.metrics())


def test_production_reset_preserves_persistent_ban(tmp_path):
    from services.history import reset_database
    path=tmp_path/"db.sqlite"
    budget=ApiBudget(path,clock=lambda:100)
    network,token=budget.reserve(URL)
    with pytest.raises(ApiQuotaError):
        budget.observe(network,Response(418,headers={"Retry-After":"3600"}),token)
    reset_database(path)
    with pytest.raises(ApiQuotaError):
        ApiBudget(path,clock=lambda:101).reserve(URL)


def test_unknown_header_interval_does_not_invent_a_ceiling_after_limit_refresh(tmp_path):
    budget=ApiBudget(tmp_path/"db.sqlite",clock=lambda:100)
    network,token=budget.reserve(URL)
    budget.observe(network,Response(headers={"X-MBX-USED-WEIGHT-1H":"500"}),token)
    budget.publish_limits("SPOT",LIMITS)
    assert not any(x["seconds"]==3600 for x in budget.metrics()["limits"])
    assert budget.metrics()["windows"]["REQUEST_WEIGHT:3600"]["observed"]==500
    budget.reserve(URL)
