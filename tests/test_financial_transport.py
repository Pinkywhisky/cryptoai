"""GET transport fault injection; all requests are Python stubs."""
from decimal import Decimal
import json

import pytest
import requests

from services.binance_private_client import (
    BinanceDataError, BinancePrivateClient, BinanceReadError, normalize_balances, normalize_trade)
from test_financial_reference import trade


@pytest.fixture
def client():
    config=type("Config",(),{"api_key":"SYNTHETIC_KEY","api_secret":"SYNTHETIC_SECRET",
                "use_testnet":False,"configured":True})()
    return BinancePrivateClient(config,sleep=lambda delay:None,min_interval=0)


class Response:
    def __init__(self,status=200,data=None,headers=None):
        self.status_code=status
        self.data=data
        self.headers=headers or {}
    def json(self):
        if isinstance(self.data,Exception):
            raise self.data
        return self.data
    def raise_for_status(self):
        if self.status_code>=400:
            raise requests.HTTPError("synthetic body containing forbidden secret")


@pytest.mark.parametrize("status",[429,500,502,503,504])
def test_safe_get_retries_with_bounded_backoff(client,monkeypatch,status):
    calls=[]
    responses=[Response(status),Response(status),Response(200,[])]
    def get(*args,**kwargs):
        calls.append(kwargs)
        return responses.pop(0)
    monkeypatch.setattr(requests,"get",get)
    sleeps=[]
    client.sleep=sleeps.append
    assert client.get_my_trades("ABCUSDC")==[]
    assert len(calls)==3
    assert len(sleeps)==2
    assert all(0<=delay<=10 for delay in sleeps)
    assert all(call["params"]["fromId"]==0 and call["params"]["limit"]==1000 for call in calls)


@pytest.mark.parametrize("error",[requests.Timeout("signed-url-and-secret"),requests.ConnectionError("signed-url-and-secret")])
def test_transport_failure_is_retried_then_sanitized(client,monkeypatch,error):
    calls=[]
    def get(*args,**kwargs):
        calls.append(True)
        raise error
    monkeypatch.setattr(requests,"get",get)
    with pytest.raises(BinanceReadError) as info:
        client.get_my_trades("ABCUSDC")
    assert len(calls)==3
    assert "signed-url" not in str(info.value)
    assert info.value.__cause__ is None


def test_retry_after_longer_than_budget_is_returned_without_early_retry(client,monkeypatch):
    calls=[]
    monkeypatch.setattr(requests,"get",lambda *a,**k:calls.append(True) or Response(429,headers={"Retry-After":"120"}))
    with pytest.raises(BinanceReadError) as info:
        client.get_my_trades("ABCUSDC")
    assert len(calls)==1
    assert info.value.retry_after==120


@pytest.mark.parametrize("status",[400,401,403,418])
def test_permanent_errors_are_not_retried(client,monkeypatch,status):
    calls=[]
    monkeypatch.setattr(requests,"get",lambda *a,**k:calls.append(True) or Response(status))
    with pytest.raises(BinanceReadError):
        client.get_my_trades("ABCUSDC")
    assert len(calls)==1


@pytest.mark.parametrize("data",[ValueError("private response"),{},{"code":-1,"msg":"private response"},None,"not a list"])
def test_invalid_trade_response_never_reaches_storage(client,monkeypatch,data):
    monkeypatch.setattr(requests,"get",lambda *a,**k:Response(200,data))
    with pytest.raises(BinanceReadError) as info:
        client.get_my_trades("ABCUSDC")
    assert "private response" not in str(info.value)


@pytest.mark.parametrize("body",[{},{"balances":None},{"balances":"[]"},
                                {"balances":[{"asset":"ABC","free":"1"}]}])
def test_missing_account_balances_are_not_an_empty_wallet(client,monkeypatch,body):
    monkeypatch.setattr(requests,"get",lambda *a,**k:Response(200,body))
    with pytest.raises(BinanceDataError):
        client.get_non_zero_balances()


@pytest.mark.parametrize("change",[
    {"id":None},{"id":True},{"time":-1},{"qty":"0"},{"qty":"NaN"},
    {"price":"Infinity"},{"commission":"-1"},{"isBuyer":"false"},{"isMaker":1},
    {"commissionAsset":None},{"symbol":"XYZUSDC"},{"quoteQty":None},
])
def test_malformed_trade_rows_are_rejected(change):
    with pytest.raises(BinanceReadError):
        normalize_trade({**trade(),**change},"ABCUSDC")


def test_decimal_balances_keep_fifty_significant_digits():
    value="1.000000000000000000000000000000000000000000000001"
    normalized=normalize_balances([{"asset":"ABC","free":value,"locked":"0"}])
    assert Decimal(normalized[0]["total_decimal"])==Decimal(value)


def test_private_transport_has_no_order_execution_surface(client,monkeypatch):
    calls=[]
    monkeypatch.setattr(requests,"get",lambda *a,**k:calls.append(True))
    for endpoint in ["/api/v3/order","/api/v3/order/test","/sapi/v1/capital/withdraw/apply"]:
        with pytest.raises(BinanceReadError):
            client._signed_get(endpoint)
    assert calls==[]
    assert not any(name in dir(client) for name in ("create_order","place_order","withdraw"))


def test_full_history_starts_at_zero_not_recent_default(client,monkeypatch):
    seen=[]
    monkeypatch.setattr(requests,"get",lambda *a,**k:seen.append(k) or Response(200,[]))
    assert client.get_my_trades("ABCUSDC")==[]
    assert seen[0]["params"]["fromId"]==0
    assert seen[0]["params"]["limit"]==1000


@pytest.mark.parametrize("testnet,base",[
    (False,"https://api.binance.com"),
    (True,"https://testnet.binance.vision"),
])
def test_public_prices_use_same_network_without_credentials(monkeypatch,testnet,base):
    config=type("Config",(),{"api_key":"SYNTHETIC_KEY","api_secret":"SYNTHETIC_SECRET",
                             "configured":True,"use_testnet":testnet})()
    client=BinancePrivateClient(config,min_interval=0,sleep=lambda delay:None)
    captured=[]
    monkeypatch.setattr(requests,"get",lambda url,**kwargs:
        captured.append((url,kwargs)) or Response(200,{"symbol":"ABCUSDC","price":"100.000000000000000001"}))
    assert client.get_market_price("ABCUSDC")==Decimal("100.000000000000000001")
    url,kwargs=captured[0]
    assert url==base+"/api/v3/ticker/price"
    assert kwargs["headers"]=={}
    assert kwargs["params"]=={"symbol":"ABCUSDC"}


@pytest.mark.parametrize("price",[None,"0","-1","NaN","Infinity"])
def test_invalid_public_price_is_never_a_zero_valuation(client,monkeypatch,price):
    monkeypatch.setattr(requests,"get",lambda *a,**k:Response(200,{"symbol":"ABCUSDC","price":price}))
    with pytest.raises(BinanceDataError):
        client.get_market_price("ABCUSDC")
