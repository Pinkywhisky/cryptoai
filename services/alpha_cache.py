"""Bounded in-memory Alpha cache with per-key single-flight and failure cooldown.

Only validated loader results are stored. Stale values are explicit snapshots;
callers must never expose them as current quotes. No background HTTP or threads.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import math
import hashlib
import json
import threading

from services.market_data_errors import MarketDataError


@dataclass(frozen=True)
class CacheRead:
    value: object
    fetched_at: float
    stale: bool = False
    error: MarketDataError | None = None
    cached: bool = False


@dataclass
class Entry:
    value: object = None
    fetched_at: float | None = None
    retry_at: float = 0
    error: MarketDataError | None = None
    failures: int = 0
    expires_at: float | None = None
    generation: int = 0
    signature: str | None = None


class AlphaCache:
    def __init__(self, clock, *, maximum=512, wait_seconds=28):
        self.clock = clock
        self.maximum = maximum
        self.wait_seconds = wait_seconds
        self._generation = 0
        self._guard = threading.RLock()
        self._entries = OrderedDict()
        self._pending = {}
        self._stats = {"hits": 0, "misses": 0, "joined": 0, "stale": 0, "failures": 0}

    def clear(self):
        with self._guard:
            if self._pending:
                raise RuntimeError("Cannot clear an active Alpha cache")
            self._entries.clear()
            self._stats = dict.fromkeys(self._stats, 0)

    def status(self, key):
        with self._guard:
            entry = self._entries.get(key)
            return {"generation": entry.generation, "signature": entry.signature, "expires_at": entry.expires_at} if entry else {}

    def metrics(self):
        with self._guard:
            return {**self._stats, "entries": len(self._entries), "inflight": len(self._pending)}

    def _fallback(self, entry, now, ttl, stale_seconds, source_stale_seconds=None):
        error = entry.error or MarketDataError("SERVICE_UNAVAILABLE", retry_after=1)
        if entry.retry_at > now:
            error = MarketDataError(error.code, service=error.service, retry_after=entry.retry_at-now, details=error.details)
        if (entry.value is not None and entry.fetched_at is not None
                and 0 <= now-entry.fetched_at <= ttl+stale_seconds
                and (entry.expires_at is None or now <= entry.expires_at+(stale_seconds if source_stale_seconds is None else source_stale_seconds))):
            self._stats["stale"] += 1
            return CacheRead(deepcopy(entry.value), entry.fetched_at, True, error, True)
        raise error

    def get(self, key, loader, *, ttl=600, stale_seconds=1800, force=False, valid_until=None, source_stale_seconds=None):
        joined = False
        while True:
            now = self.clock()
            with self._guard:
                entry = self._entries.get(key)
                if entry is not None:
                    self._entries.move_to_end(key)
                if (entry and entry.value is not None and entry.fetched_at is not None
                        and 0 <= now-entry.fetched_at < ttl and (not force or joined)
                        and (entry.expires_at is None or now < entry.expires_at)):
                    self._stats["hits"] += 1
                    return CacheRead(deepcopy(entry.value), entry.fetched_at, cached=True)
                if entry and entry.retry_at > now:
                    return self._fallback(entry, now, ttl, stale_seconds, source_stale_seconds)
                pending = self._pending.get(key)
                if pending is None:
                    pending = threading.Event()
                    self._pending[key] = pending
                    self._stats["misses"] += 1
                    break
                self._stats["joined"] += 1
                if entry and entry.value is not None:
                    # Another caller refreshes this key. Return explicit stale
                    # data immediately rather than holding every cockpit reader.
                    return self._fallback(entry, now, ttl, stale_seconds, source_stale_seconds)
            if not pending.wait(self.wait_seconds):
                raise MarketDataError("BUSY", retry_after=1)
            joined = True
        try:
            value = loader()
            now = self.clock()
            if not math.isfinite(now):
                raise MarketDataError("INVALID_CONFIGURATION")
            expires = valid_until(value) if valid_until is not None else now+ttl
            if type(expires) not in {int, float} or not math.isfinite(expires):
                raise MarketDataError("INVALID_RESPONSE")
            expires = min(expires, now+ttl)
            with self._guard:
                previous = self._entries.get(key)
                self._generation += 1
                entry = Entry(deepcopy(value), now, expires_at=expires, generation=self._generation)
                if key in {("catalog",), ("markets",)}:
                    entry.signature = hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
                if expires <= now:
                    # A successful HTTP response can itself contain an old quote.
                    # Keep it only as a dated snapshot and retry after a cooldown.
                    if previous and previous.expires_at is not None and previous.expires_at > expires:
                        entry = previous
                    entry.failures = (previous.failures if previous else 0)+1
                    entry.error = MarketDataError("STALE_DATA", retry_after=5)
                    entry.retry_at = now+max(5, min(60, 2 ** min(entry.failures, 6)))
                    self._stats["failures"] += 1
                self._entries[key] = entry
                self._entries.move_to_end(key)
                while len(self._entries) > self.maximum:
                    self._entries.popitem(last=False)
                if expires <= now:
                    return self._fallback(entry, now, ttl, stale_seconds, source_stale_seconds)
            return CacheRead(deepcopy(value), now)
        except MarketDataError as error:
            now = self.clock()
            with self._guard:
                entry = self._entries.get(key) or Entry()
                if error.code == "INVALID_PAIR":
                    entry.value = None
                    entry.fetched_at = None
                entry.failures += 1
                entry.error = error
                delay = error.retry_after if error.retry_after is not None else min(60, 2 ** min(entry.failures, 6))
                entry.retry_at = now+delay
                self._entries[key] = entry
                self._entries.move_to_end(key)
                self._stats["failures"] += 1
                while len(self._entries) > self.maximum:
                    self._entries.popitem(last=False)
                return self._fallback(entry, now, ttl, stale_seconds, source_stale_seconds)
        finally:
            with self._guard:
                self._pending.pop(key, None)
                pending.set()
