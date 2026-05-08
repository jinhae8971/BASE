"""Token-bucket rate limiter for the KIS Open API.

KIS publishes a per-second cap (typically 20 req/s for paper, 5 req/s for
live retail). Bursting past it returns 500.001 / EGW00201 and locks our
account briefly. We throttle locally so we never depend on retry-on-error.

Usage::

    limiter = TokenBucket(rate=5, capacity=10)
    limiter.acquire()  # blocks until a token is available

The bucket is process-local; the scheduler runs in a single process so
that's enough. If we ever go multi-process we'll move to a Redis-backed
limiter.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class TokenBucket:
    rate: float           # tokens per second refill
    capacity: int         # max burst
    _tokens: float = 0.0
    _last_refill: float = 0.0
    _lock: threading.Lock | None = None

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(
            self.capacity, self._tokens + elapsed * self.rate
        )
        self._last_refill = now

    def try_acquire(self, n: int = 1) -> bool:
        assert self._lock is not None
        with self._lock:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def acquire(self, n: int = 1, *, timeout: float | None = None) -> None:
        """Block until ``n`` tokens are available. Raises on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self.try_acquire(n):
                return
            sleep_for = max(0.001, n / self.rate)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("rate limiter timeout")
                sleep_for = min(sleep_for, remaining)
            time.sleep(sleep_for)


_DEFAULT: TokenBucket | None = None


def get_default_limiter() -> TokenBucket:
    """Singleton sized from settings ``broker.rate_limit_*``."""
    global _DEFAULT
    if _DEFAULT is None:
        from common.config import get_setting

        rate = float(get_setting("broker.rate_limit_per_sec", 5.0))
        burst = int(get_setting("broker.rate_limit_burst", 10))
        _DEFAULT = TokenBucket(rate=rate, capacity=burst)
    return _DEFAULT


def reset_default_for_tests() -> None:
    """Test hook only — drops the singleton."""
    global _DEFAULT
    _DEFAULT = None
