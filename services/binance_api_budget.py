"""Shared durable Binance quotas; public metadata is authoritative, headers are usage.

Spot fallback: documented 6000 weight/minute and 61000 raw requests/5 minutes,
before the configurable safety margin. Testnet, Alpha and Wallet have separate
policies. Missing headers never refund unconfirmed requests. No quota sleeps.
"""
from contextlib import closing
from email.utils import parsedate_to_datetime
import json
import math
import os
import re
import sqlite3
import time
import uuid
from urllib.parse import urlsplit

import requests

from services.history import DB_PATH, init_db


class ApiQuotaError(requests.RequestException):
    def __init__(self, reason, retry_after):
        super().__init__("Binance API calls suspended")
        self.code = reason
        self.retry_after = max(1, math.ceil(retry_after))


def endpoint_weight(path, params=None):
    params = params or {}
    weights = {"/api/v3/account": 20, "/api/v3/exchangeInfo": 20,
               "/api/v3/klines": 2, "/api/v3/uiKlines": 2, "/api/v3/allOrders": 20,
               "/sapi/v1/accountSnapshot": 2400}
    if path == "/api/v3/myTrades":
        return 5 if "orderId" in params else 20
    if path in {"/api/v3/ticker/price", "/api/v3/ticker/bookTicker"}:
        return 2 if params.get("symbol") else 4
    if path == "/api/v3/ticker/24hr":
        if params.get("symbol"):
            return 2
        symbols = params.get("symbols")
        if isinstance(symbols, str):
            try:
                symbols = json.loads(symbols)
            except ValueError:
                symbols = None
        if isinstance(symbols, (list, tuple)) and symbols:
            return 2 if len(symbols) <= 20 else 40 if len(symbols) <= 100 else 80
        return 80
    return weights.get(path, 80)  # Local estimate for undocumented Alpha weights.


def network_for(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if host == "testnet.binance.vision":
        network = "SPOT_TESTNET"
    elif host == "www.binance.com":
        network = "ALPHA"
    elif host in {"api.binance.com", "api-gcp.binance.com", "data-api.binance.vision"} or re.fullmatch(r"api[1-4]\.binance\.com", host):
        network = "SPOT"
    else:
        raise ValueError("Unsupported Binance transport host")
    if parsed.path.startswith("/sapi/"):
        # Only this IP-limited Wallet endpoint is used by the read-only client.
        # SAPI quotas are per endpoint, independent from /api/* Spot weight.
        if parsed.path != "/sapi/v1/accountSnapshot" or network == "ALPHA":
            raise ValueError("Unsupported Binance Wallet quota")
        return network + "_SNAPSHOT"
    return network


def _configured_limit(name, default):
    raw = os.getenv("CRYPTOAI_API_" + name)
    try:
        value = int(raw) if raw is not None else default
        if not 1 <= value <= 10**9:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Invalid API fallback setting: " + name) from None
    return value, "CONFIGURED_FALLBACK" if raw is not None else "DOCUMENTED_FALLBACK"


def _fallback(network):
    policies = {
        "SPOT": ("SPOT", 6000, 61000, 300),
        "SPOT_TESTNET": ("TESTNET", 600, 600, 60),
        "ALPHA": ("ALPHA", 600, 600, 60),
        "SPOT_SNAPSHOT": ("SNAPSHOT", 12000, 600, 60),
        "SPOT_TESTNET_SNAPSHOT": ("TESTNET_SNAPSHOT", 600, 600, 60),
    }
    if network not in policies:
        raise ValueError("Unsupported Binance quota network")
    prefix, weight, raw, raw_seconds = policies[network]
    weight, weight_source = _configured_limit(prefix + "_WEIGHT_PER_MINUTE", weight)
    raw, raw_source = _configured_limit(prefix + "_RAW_REQUESTS", raw)
    if network not in {"SPOT", "SPOT_SNAPSHOT"} and weight_source != "CONFIGURED_FALLBACK":
        weight_source = "CONSERVATIVE_FALLBACK"
    if network != "SPOT" and raw_source != "CONFIGURED_FALLBACK":
        raw_source = "CONSERVATIVE_FALLBACK"
    return [{"metric": "REQUEST_WEIGHT", "seconds": 60, "limit": weight, "source": weight_source},
            {"metric": "RAW_REQUESTS", "seconds": raw_seconds, "limit": raw, "source": raw_source}]


def _fresh(network):
    return {"limits": _fallback(network), "policy_version": 2,
            "windows": {}, "blocked_until": 0, "block_reason": None, "requests": 0, "weight": 0,
            "missing_headers": 0, "invalid_headers": 0, "by_endpoint": {}, "published": False,
            "limits_at": None, "outstanding": {}}


class ApiBudget:
    def __init__(self, path=DB_PATH, *, clock=time.time, safety=None):
        self.path = path
        self.clock = clock
        self.safety = float(os.getenv("CRYPTOAI_API_SAFETY", "0.8") if safety is None else safety)
        if not math.isfinite(self.safety) or not .1 <= self.safety <= .95:
            raise ValueError("API safety must be between 0.1 and 0.95")

    def _open(self):
        init_db(self.path)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            connection.close()
            raise
        return connection

    def _read(self, connection, network):
        row = connection.execute("SELECT payload_json FROM binance_api_state WHERE network=?", (network,)).fetchone()
        if not row:
            return _fresh(network)
        state = json.loads(row[0])
        # Upgrade the JSON policy, never the financial schema, retaining bans and
        # all old consumption. A header reports a count, never a numeric ceiling.
        if state.get("policy_version") != 2:
            official = [x for x in state["limits"] if x["source"] == "EXCHANGE_INFO"]
            defaults = _fallback(network)
            state["limits"] = official + [x for x in defaults if not any(
                y["metric"] == x["metric"] for y in official)]
            state["policy_version"] = 2
        # Configuration can change between launches; it only replaces fallback
        # policy, never previously observed official limits.
        official = [x for x in state["limits"] if x["source"] == "EXCHANGE_INFO"]
        state["limits"] = official + [x for x in _fallback(network) if not any(
            y["metric"] == x["metric"] for y in official)]
        state.setdefault("limits_at", None)
        state.setdefault("outstanding", {})
        return state

    def _save(self, connection, network, state):
        connection.execute("""INSERT INTO binance_api_state VALUES(?,?)
            ON CONFLICT(network) DO UPDATE SET payload_json=excluded.payload_json""",
            (network, json.dumps(state, allow_nan=False, separators=(",", ":"))))

    @staticmethod
    def _usage(window):
        # Confirmed remote usage cannot decrease in a window. Pending or
        # headerless requests are added until their own header confirms inclusion.
        window["used"] = max(window["local"], window["legacy_floor"],
                             window["observed"] + window["unconfirmed"])
        return window["used"]

    def _window(self, state, metric, seconds, now):
        key = metric + ":" + str(seconds)
        start = math.floor(now / seconds) * seconds
        item = state["windows"].get(key)
        if item is None or item["start"] < start:
            # In-flight requests may be processed across a boundary. Reserve them
            # in the new window too until a dated response proves otherwise.
            pending = 0
            for record in state["outstanding"].values():
                pending += record["weight"] if metric == "REQUEST_WEIGHT" else 1
                record.setdefault("charged", {})[key] = start
            item = {"start": start, "used": pending, "local": pending,
                    "unconfirmed": pending, "observed": 0, "legacy_floor": 0}
            state["windows"][key] = item
        elif "local" not in item:
            item.update(local=item["used"], unconfirmed=0, observed=0, legacy_floor=item["used"])
        return item

    def _prepare(self, state, now):
        # Current transports time out within 15 seconds; retain ample overlap.
        # Abandoned reservations are never refunded inside their existing window.
        state["outstanding"] = {k: v for k, v in state["outstanding"].items() if v["at"] >= now - 120}
        tracked = {(x["metric"], x["seconds"]) for x in state["limits"]}
        tracked.update((key.split(":")[0], int(key.split(":")[1])) for key in state["windows"])
        return [(metric, seconds, self._window(state, metric, seconds, now))
                for metric, seconds in sorted(tracked)]

    def reserve(self, url, params=None):
        network = network_for(url)
        path = urlsplit(url).path
        weight = endpoint_weight(path, params)
        token = uuid.uuid4().hex
        suspended = None
        with closing(self._open()) as connection, connection:
            # Read time after acquiring the cross-process lock, not before it.
            now = self.clock()
            state = self._read(connection, network)
            tracked = self._prepare(state, now)
            if state["blocked_until"] > now:
                suspended = ApiQuotaError(state["block_reason"], state["blocked_until"] - now)
            else:
                wait = 0
                for limit in state["limits"]:
                    window = self._window(state, limit["metric"], limit["seconds"], now)
                    amount = weight if limit["metric"] == "REQUEST_WEIGHT" else 1
                    if self._usage(window) + amount > math.floor(limit["limit"] * self.safety):
                        wait = max(wait, window["start"] + limit["seconds"] - now)
                if wait:
                    suspended = ApiQuotaError("LOCAL_QUOTA", wait)
                else:
                    charged = {}
                    for metric, seconds, window in tracked:
                        amount = weight if metric == "REQUEST_WEIGHT" else 1
                        window["local"] += amount
                        window["unconfirmed"] += amount
                        self._usage(window)
                        charged[metric + ":" + str(seconds)] = window["start"]
                    state["outstanding"][token] = {"weight": weight, "at": now, "charged": charged}
                    state["requests"] += 1
                    state["weight"] += weight
                    by = state["by_endpoint"].setdefault(path, {"requests": 0, "weight": 0})
                    by["requests"] += 1
                    by["weight"] += weight
            self._save(connection, network, state)
        if suspended:
            raise suspended
        return network, token

    def observe(self, network, response, token=None):
        headers = getattr(response, "headers", {}) or {}
        items = list(headers.items()) if hasattr(headers, "items") else []
        # Keep duplicates with different case: use the greatest valid counter.
        values = [(str(k).lower(), str(v)) for k, v in items]
        status = getattr(response, "status_code", 200)
        suspended = None
        with closing(self._open()) as connection, connection:
            now = self.clock()
            state = self._read(connection, network)
            self._prepare(state, now)
            record = state["outstanding"].get(token)
            response_time = now
            dated = False
            for key, value in values:
                if key == "date":
                    try:
                        stamp = parsedate_to_datetime(value).timestamp()
                        if not math.isfinite(stamp) or not now - 120 <= stamp <= now + 5:
                            raise ValueError
                        response_time, dated = min(stamp, now), True
                    except (TypeError, ValueError, OverflowError):
                        state["invalid_headers"] += 1
            matched = False
            for key, value in values:
                pattern = r"x-sapi-used-ip-weight-(\d+)([smhd])" if network.endswith("_SNAPSHOT") else r"x-mbx-used-weight-(\d+)([smhd])"
                match = re.fullmatch(pattern, key) if network != "ALPHA" else None
                if not match:
                    continue
                matched = True
                try:
                    if not re.fullmatch(r"[0-9]{1,13}", value):
                        raise ValueError
                    count = int(value)
                    number = int(match[1])
                    seconds = number * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
                    if not 0 <= count <= 10**12 or not 1 <= seconds <= 86400 * 31:
                        raise ValueError
                    window = self._window(state, "REQUEST_WEIGHT", seconds, now)
                    metric_key = "REQUEST_WEIGHT:" + str(seconds)
                    if math.floor(response_time / seconds) * seconds < window["start"]:
                        # Do not inject last minute's high count into this minute.
                        if dated and record and record["at"] < window["start"] and record.get("charged", {}).get(metric_key) == window["start"]:
                            window["local"] = max(0, window["local"] - record["weight"])
                            window["unconfirmed"] = max(0, window["unconfirmed"] - record["weight"])
                            record["charged"].pop(metric_key, None)
                    else:
                        window["observed"] = max(window["observed"], count)
                        if record and record.get("charged", {}).get(metric_key) == window["start"]:
                            window["unconfirmed"] = max(0, window["unconfirmed"] - record["weight"])
                            record["charged"].pop(metric_key, None)
                    self._usage(window)
                    # Unknown intervals are observable, not invented 600-unit limits.
                except (ValueError, OverflowError):
                    state["invalid_headers"] += 1
            if not matched:
                state["missing_headers"] += 1
            state["outstanding"].pop(token, None)
            if status in {429, 418}:
                default = 60 if status == 429 else 3 * 86400
                waits = []
                for key, raw in values:
                    if key != "retry-after":
                        continue
                    try:
                        try:
                            wait = float(raw)
                        except ValueError:
                            wait = parsedate_to_datetime(raw).timestamp() - now
                        if not math.isfinite(wait) or wait < 0:
                            raise ValueError
                        waits.append(max(1, wait))
                    except (TypeError, ValueError, OverflowError):
                        waits.append(default)
                until = now + max(waits or [default])
                if until >= state["blocked_until"]:
                    state["blocked_until"] = until
                    state["block_reason"] = "HTTP_418" if status == 418 else "RATE_LIMIT"
                suspended = ApiQuotaError(state["block_reason"], state["blocked_until"] - now)
            self._save(connection, network, state)
        if suspended:
            raise suspended

    def finish_failed(self, network, token):
        # Timeouts can consume remote weight. Leave unconfirmed usage charged.
        with closing(self._open()) as connection, connection:
            state = self._read(connection, network)
            state["outstanding"].pop(token, None)
            self._save(connection, network, state)

    def publish_limits(self, network, payload, *, observed_at=None, from_cache=False):
        if network not in {"SPOT", "SPOT_TESTNET"} or not isinstance(payload, list):
            return False
        limits = []
        for item in payload:
            metric = item.get("rateLimitType") if isinstance(item, dict) else None
            if not isinstance(metric, str) or metric not in {"REQUEST_WEIGHT", "RAW_REQUESTS"}:
                continue
            try:
                interval, number, maximum = item["interval"], item["intervalNum"], item["limit"]
                if type(number) is not int or type(maximum) is not int:
                    raise ValueError
                seconds = number * {"SECOND": 1, "MINUTE": 60, "HOUR": 3600, "DAY": 86400}[interval]
                if not 1 <= seconds <= 86400 * 31 or not 1 <= maximum <= 10**9:
                    raise ValueError
                limits.append({"metric": item["rateLimitType"], "seconds": seconds,
                               "limit": maximum, "source": "EXCHANGE_INFO"})
            except (KeyError, TypeError, ValueError):
                return False
        if not any(x["metric"] == "REQUEST_WEIGHT" for x in limits):
            return False
        if len({(x["metric"], x["seconds"]) for x in limits}) != len(limits):
            return False
        with closing(self._open()) as connection, connection:
            now = self.clock()
            observed_at = now if observed_at is None else observed_at
            if type(observed_at) not in {int, float} or not math.isfinite(observed_at) or observed_at > now:
                return False
            state = self._read(connection, network)
            if (from_cache and state["published"]) or (state["limits_at"] is not None and observed_at < state["limits_at"]):
                return False
            old = list(state["limits"])
            if observed_at == state["limits_at"]:
                # Requests can start at the same clock tick. Prefer the stricter
                # official observation when their order cannot be established.
                for previous in old:
                    if previous["source"] != "EXCHANGE_INFO":
                        continue
                    matching = next((x for x in limits if x["metric"] == previous["metric"]
                                     and x["seconds"] == previous["seconds"]), None)
                    if matching is None:
                        limits.append(previous)
                    else:
                        matching["limit"] = min(matching["limit"], previous["limit"])
            # Missing RAW metadata cannot replace an already published RAW limit.
            if not any(x["metric"] == "RAW_REQUESTS" for x in limits):
                limits += [x for x in old if x["metric"] == "RAW_REQUESTS"]
            self._prepare(state, now)
            for limit in limits:
                previously_enforced = any(x["metric"] == limit["metric"] and x["seconds"] == limit["seconds"] for x in old)
                window = self._window(state, limit["metric"], limit["seconds"], now)
                if not previously_enforced:
                    # Exact pre-publication distribution in a new interval is
                    # unknown. Keep the known active counts until its boundary,
                    # including when the new official window is shorter.
                    window["legacy_floor"] = max([window["legacy_floor"]] + [
                        self._usage(self._window(state, x["metric"], x["seconds"], now))
                        for x in old if x["metric"] == limit["metric"]])
                self._usage(window)
            state["limits"], state["published"], state["limits_at"] = limits, True, observed_at
            self._save(connection, network, state)
        return True

    def metrics(self, network="SPOT"):
        init_db(self.path)
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            state = self._read(connection, network)
        now = self.clock()
        self._prepare(state, now)
        result = {k: state[k] for k in ("requests", "weight", "missing_headers", "invalid_headers", "by_endpoint", "published", "limits_at")}
        result.update(safety=self.safety, blocked_until=state["blocked_until"],
                      block_reason=state["block_reason"] if state["blocked_until"] > now else None,
                      retry_after=max(0, math.ceil(state["blocked_until"] - now)), limits=state["limits"])
        result["windows"] = {k: dict(v) for k, v in state["windows"].items()}
        return result


def budgeted_get(url, *, _budget=None, **kwargs):
    budget = _budget or ApiBudget()
    started = budget.clock()
    network, token = budget.reserve(url, kwargs.get("params"))
    try:
        # Redirects would issue unreserved requests, potentially forwarding signed
        # query parameters. Binance API endpoints are final HTTPS URLs.
        response = requests.get(url, **(kwargs | {"allow_redirects": False}))
    except requests.RequestException:
        budget.finish_failed(network, token)
        raise
    budget.observe(network, response, token)
    if 300 <= getattr(response, "status_code", 200) < 400:
        raise requests.RequestException("Unexpected Binance redirect")
    if urlsplit(url).path == "/api/v3/exchangeInfo" and getattr(response, "status_code", 200) == 200:
        try:
            payload = response.json()
        except (ValueError, requests.RequestException):
            payload = None
        if isinstance(payload, dict):
            budget.publish_limits(network, payload.get("rateLimits"), observed_at=started)
    return response
