"""Read-only Binance GET transport with bounded retries and validated envelopes."""
import hashlib
from decimal import localcontext
import hmac
import re
import threading
import time
from typing import Any
from urllib.parse import urlencode

import requests

from services.config import BinancePrivateConfig, get_binance_private_config
from services.pnl import decimal_value
from services.binance_api_budget import ApiBudget, ApiQuotaError, budgeted_get

SPOT_BASE_URL = "https://api.binance.com"
SPOT_TESTNET_BASE_URL = "https://testnet.binance.vision"
READ_ENDPOINTS = {"/api/v3/account", "/api/v3/myTrades", "/api/v3/allOrders",
                  "/sapi/v1/accountSnapshot"}
ASSET = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,39}$")


class BinanceReadError(RuntimeError):
    def __init__(self, code="UNAVAILABLE", *, retry_after=None):
        # Never include request URLs, signed parameters, response bodies or keys.
        super().__init__("Binance read unavailable")
        self.code = code
        self.retry_after = retry_after


class BinanceDataError(BinanceReadError):
    pass


def integer(value, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 9223372036854775807:
        raise BinanceDataError("INVALID_INTEGER")
    return value


def normalize_trade(trade, symbol):
    if not isinstance(trade, dict):
        raise BinanceDataError("INVALID_TRADE")
    try:
        market = symbol.upper()
        if trade.get("symbol", market) != market or not ASSET.fullmatch(market):
            raise ValueError
        identity = integer(trade.get("id", trade.get("trade_id")))
        timestamp = integer(trade.get("time"))
        order = integer(trade.get("orderId", trade.get("order_id")))
        buyer = trade.get("isBuyer", trade.get("is_buyer"))
        maker = trade.get("isMaker", trade.get("is_maker"))
        if not isinstance(buyer, bool) or not isinstance(maker, bool):
            raise ValueError
        values = {name: decimal_value(trade.get(raw, trade.get(name)), nonnegative=True)
                  for name,raw in (("price","price"),("qty","qty"),("quote_qty","quoteQty"),
                                   ("commission","commission"))}
        if values["price"] <= 0 or values["qty"] <= 0 or values["quote_qty"] <= 0:
            raise ValueError
        asset = trade.get("commissionAsset", trade.get("commission_asset"))
        if not isinstance(asset, str) or not ASSET.fullmatch(asset):
            raise ValueError
        return {"symbol":market,"trade_id":identity,"order_id":order,
                "time":timestamp,"is_buyer":int(buyer),"is_maker":int(maker),
                "side":"BUY" if buyer else "SELL","commission_asset":asset,
                **{name:str(value) for name,value in values.items()}}
    except (ValueError, TypeError, KeyError):
        raise BinanceDataError("INVALID_TRADE") from None


def _normalize_balances(balances):
    if not isinstance(balances, list):
        raise BinanceDataError("INVALID_BALANCES")
    normalized = []
    seen = set()
    try:
        for item in balances:
            asset = item["asset"]
            if not isinstance(asset,str) or not ASSET.fullmatch(asset) or asset in seen:
                raise ValueError
            free = decimal_value(item.get("free_decimal",item["free"]),nonnegative=True)
            locked = decimal_value(item.get("locked_decimal",item["locked"]),nonnegative=True)
            for field,exact in (("free",free),("locked",locked)):
                approximate=decimal_value(item[field],nonnegative=True)
                if abs(approximate-exact)>max(abs(approximate),abs(exact))*decimal_value("1e-12"):
                    raise ValueError
            total = free+locked
            supplied = item.get("total_decimal", item.get("total"))
            if supplied is not None:
                supplied = decimal_value(supplied, nonnegative=True)
                if abs(supplied-total) > max(total, supplied)*decimal_value("1e-12"):
                    raise ValueError
            seen.add(asset)
            if total > 0:
                normalized.append({"asset":asset,"free":float(free),"locked":float(locked),
                                   "total":float(total),"free_decimal":str(free),
                                   "locked_decimal":str(locked),"total_decimal":str(total)})
        return normalized
    except (ValueError,TypeError,KeyError,OverflowError):
        raise BinanceDataError("INVALID_BALANCES") from None


def normalize_balances(balances):
    with localcontext() as context:
        context.prec=256
        return _normalize_balances(balances)


class BinancePrivateClient:
    def __init__(self, config: BinancePrivateConfig | None = None, *,
                 max_attempts=3, sleep=time.sleep, min_interval=0, budget=None):
        self.config = config or get_binance_private_config()
        self.base_url = SPOT_TESTNET_BASE_URL if self.config.use_testnet else SPOT_BASE_URL
        if not 1 <= max_attempts <= 5 or not 0 <= min_interval <= 10:
            raise ValueError("Invalid retry policy")
        self.max_attempts = max_attempts
        self.budget = budget or ApiBudget()
        self.request_hook = None
        self.sleep = sleep
        self.min_interval = min_interval
        self._last_request = None
        self._request_lock = threading.Lock()

    @property
    def configured(self):
        return self.config.configured

    def _timestamp(self):
        return int(time.time()*1000)

    def _sign(self, params):
        if not self.config.api_secret:
            raise BinanceReadError("NOT_CONFIGURED")
        return hmac.new(self.config.api_secret.encode(), urlencode(params,doseq=True).encode(),
                        hashlib.sha256).hexdigest()

    def _get(self, path, params, *, signed):
        if signed and (path not in READ_ENDPOINTS or not self.configured):
            raise BinanceReadError("NOT_CONFIGURED_OR_NOT_READ_ONLY")
        if not signed and path not in {"/api/v3/exchangeInfo","/api/v3/ticker/price"}:
            raise BinanceReadError("UNSUPPORTED_PUBLIC_ENDPOINT")
        with self._request_lock:
            for attempt in range(self.max_attempts):
                if self._last_request is not None:
                    delay = self.min_interval-(time.monotonic()-self._last_request)
                    if delay > 0:
                        self.sleep(delay)
                if self.request_hook is not None:
                    self.request_hook()
                request_params = dict(params or {})
                headers = {}
                if signed:
                    request_params.update(timestamp=self._timestamp(),recvWindow=5000)
                    request_params["signature"] = self._sign(request_params)
                    headers["X-MBX-APIKEY"] = self.config.api_key or ""
                self._last_request = time.monotonic()
                try:
                    response = budgeted_get(self.base_url+path,_budget=self.budget,params=request_params,
                                            headers=headers,timeout=(5,15))
                except ApiQuotaError as error:
                    raise BinanceReadError(error.code,retry_after=error.retry_after) from None
                except (requests.Timeout, requests.ConnectionError):
                    if attempt+1 < self.max_attempts:
                        self.sleep(0.25*2**attempt)
                        continue
                    raise BinanceReadError("TRANSPORT") from None
                except requests.RequestException:
                    raise BinanceReadError("TRANSPORT") from None
                status = getattr(response,"status_code",200)
                if status in {429,500,502,503,504}:
                    try:
                        wait = float(getattr(response,"headers",{}).get("Retry-After",0.25*2**attempt))
                    except (TypeError,ValueError):
                        wait = 0.25*2**attempt
                    if not 0 <= wait <= 10:
                        raise BinanceReadError("RETRY_LATER",retry_after=int(wait) if 0 <= wait <= 86400 else None)
                    if attempt+1 < self.max_attempts:
                        self.sleep(wait)
                        continue
                    raise BinanceReadError("RATE_LIMIT" if status==429 else "SERVER",
                                           retry_after=max(1,int(wait)))
                if status >= 400:
                    if status==400:
                        try:
                            error_body=response.json()
                        except (ValueError,requests.RequestException):
                            error_body=None
                        if isinstance(error_body,dict) and error_body.get("code")==-1121:
                            raise BinanceReadError("UNKNOWN_SYMBOL")
                    raise BinanceReadError("HTTP_"+str(status))
                try:
                    response.raise_for_status()
                    data = response.json()
                except (ValueError,requests.RequestException):
                    raise BinanceDataError("INVALID_JSON_OR_STATUS") from None
                if isinstance(data,dict) and data.get("code") not in (None,0):
                    raise BinanceReadError("UNKNOWN_SYMBOL" if data.get("code")==-1121 else "API_ERROR")
                return data
        raise BinanceReadError()

    def _signed_get(self,path,params=None):
        return self._get(path,params,signed=True)

    def get_account_balances(self):
        account = self._signed_get("/api/v3/account",{"omitZeroBalances":"false"})
        if not isinstance(account,dict) or "balances" not in account or not isinstance(account["balances"],list):
            raise BinanceDataError("MISSING_BALANCES")
        # Remote aliases must never override the authoritative API fields.
        try:
            balances=[{name:item[name] for name in ("asset","free","locked")}
                      for item in account["balances"]]
        except (TypeError,KeyError):
            raise BinanceDataError("INVALID_BALANCES") from None
        # Validate all rows before a caller can interpret absence as zero.
        normalize_balances(balances)
        return balances

    def get_non_zero_balances(self):
        return normalize_balances(self.get_account_balances())

    def get_my_trades(self,symbol,*,from_id=0,limit=1000):
        integer(from_id)
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1 <= limit <= 1000:
            raise ValueError("Invalid trade page limit")
        data = self._signed_get("/api/v3/myTrades",{"symbol":symbol.upper(),
                                                  "fromId":from_id,"limit":limit})
        if not isinstance(data,list) or len(data)>limit:
            raise BinanceDataError("INVALID_TRADE_PAGE")
        for trade in data:
            normalize_trade(trade,symbol)
        return data

    def get_spot_markets(self):
        data = self._get("/api/v3/exchangeInfo",None,signed=False)
        if not isinstance(data,dict) or not isinstance(data.get("symbols"),list) or not data["symbols"]:
            raise BinanceDataError("INVALID_MARKET_CATALOG")
        self.catalog_rate_limits = data.get("rateLimits")
        markets = []
        for item in data["symbols"]:
            if not isinstance(item,dict) or not all(isinstance(item.get(name),str) and ASSET.fullmatch(item[name])
                for name in ("symbol","baseAsset","quoteAsset")):
                raise BinanceDataError("INVALID_MARKET_CATALOG")
            if item["symbol"]!=item["baseAsset"]+item["quoteAsset"]:
                raise BinanceDataError("INVALID_MARKET_CATALOG")
            markets.append({"symbol":item["symbol"],"base_asset":item["baseAsset"],
                            "quote_asset":item["quoteAsset"],"status":item.get("status","UNKNOWN")})
        if len({row["symbol"] for row in markets}) != len(markets):
            raise BinanceDataError("DUPLICATE_MARKET")
        return markets

    def get_market_price(self,symbol):
        data=self._get("/api/v3/ticker/price",{"symbol":symbol.upper()},signed=False)
        try:
            if not isinstance(data,dict) or data.get("symbol",symbol.upper())!=symbol.upper():
                raise ValueError
            value=decimal_value(data.get("price"),nonnegative=True)
            if value<=0:
                raise ValueError
            return value
        except (ValueError,TypeError):
            raise BinanceDataError("INVALID_PRICE") from None

    def get_order_history(self,symbol):
        return self._signed_get("/api/v3/allOrders",{"symbol":symbol.upper()})

    def get_account_snapshot(self):
        return self._signed_get("/sapi/v1/accountSnapshot",{"type":"SPOT"})

    def get_current_holdings(self):
        return self.get_non_zero_balances()


def get_private_client_status():
    config = get_binance_private_config()
    return {"configured":config.configured,"read_only_mode":True,"use_testnet":config.use_testnet}


def get_account_balances():
    return BinancePrivateClient().get_account_balances()


def get_non_zero_balances():
    return BinancePrivateClient().get_non_zero_balances()


def get_my_trades(symbol,**kwargs):
    return BinancePrivateClient().get_my_trades(symbol,**kwargs)


def get_order_history(symbol):
    return BinancePrivateClient().get_order_history(symbol)


def get_account_snapshot():
    return BinancePrivateClient().get_account_snapshot()


def get_current_holdings():
    return BinancePrivateClient().get_current_holdings()
