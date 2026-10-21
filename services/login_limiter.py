"""Bounded single-process login throttling. No account-wide failure lockout."""

import ipaddress
import math
import threading
from collections import OrderedDict, deque

from fastapi import HTTPException


def address_key(value: str) -> str:
    if "%" in value:
        return "unknown"
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return "unknown"
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        # A single IPv6 allocation must not bypass the budget by rotating /128s.
        return str(ipaddress.ip_network((address, 64), strict=False))
    return str(address)


class LoginLimiter:
    window = 60
    per_address = 5
    max_addresses = 4096
    max_verifications = 2

    def __init__(self, clock):
        self.clock = clock
        self.attempts = OrderedDict()
        self.failures = deque(maxlen=60)
        self.lock = threading.Lock()
        self.verifications = threading.BoundedSemaphore(self.max_verifications)

    def reserve(self, address: str) -> float:
        now = self.clock()
        key = address_key(address)
        with self.lock:
            for old_key in list(self.attempts):
                queue = self.attempts[old_key]
                while queue and queue[0] <= now - self.window:
                    queue.popleft()
                if not queue:
                    del self.attempts[old_key]
            while self.failures and self.failures[0] <= now - self.window:
                self.failures.popleft()
            queue = self.attempts.get(key)
            if queue is not None and len(queue) >= self.per_address:
                retry = max(1, math.ceil(queue[0] + self.window - now))
                raise HTTPException(429, "Trop de tentatives. Reessayez plus tard.", headers={"Retry-After": str(retry)})
            if queue is None:
                if len(self.attempts) >= self.max_addresses:
                    self.attempts.popitem(last=False)
                queue = self.attempts[key] = deque()
            self.attempts.move_to_end(key)
            queue.append(now)
            # Distributed failures slow all NEW verifications, but never reject a
            # correct password solely because other addresses failed.
            return min(1.0, max(0, len(self.failures) - 19) * 0.05)

    def finish(self, address: str, valid: bool):
        with self.lock:
            if valid:
                self.attempts.pop(address_key(address), None)
            else:
                self.failures.append(self.clock())

    def acquire(self):
        if not self.verifications.acquire(blocking=False):
            raise HTTPException(429, "Connexion occupee. Reessayez dans un instant.", headers={"Retry-After": "1"})

    def release(self):
        self.verifications.release()
