"""TokenBucket — verify the throttle actually slows down a burst."""
from __future__ import annotations

import time

from broker.rate_limiter import TokenBucket


def test_initial_burst_passes_immediately() -> None:
    """capacity=5 must allow 5 rapid acquires without any wait."""
    bucket = TokenBucket(rate=1.0, capacity=5)
    t0 = time.monotonic()
    for _ in range(5):
        assert bucket.try_acquire() is True
    assert time.monotonic() - t0 < 0.05
    # 6th try should fail (no refill yet)
    assert bucket.try_acquire() is False


def test_acquire_blocks_until_refill() -> None:
    """rate=10/s + capacity=1: 1st pass instant, 2nd waits ~0.1s."""
    bucket = TokenBucket(rate=10.0, capacity=1)
    bucket.acquire()  # consume the initial token
    t0 = time.monotonic()
    bucket.acquire()
    elapsed = time.monotonic() - t0
    assert 0.05 < elapsed < 0.5  # tolerant for slow CI


def test_acquire_timeout_raises() -> None:
    """rate too slow + small timeout → TimeoutError."""
    import pytest

    bucket = TokenBucket(rate=0.1, capacity=1)
    bucket.acquire()  # consume
    with pytest.raises(TimeoutError):
        bucket.acquire(timeout=0.05)


def test_default_limiter_singleton(monkeypatch) -> None:
    from broker import rate_limiter as r
    from common import config as c

    r.reset_default_for_tests()
    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {"broker": {"rate_limit_per_sec": 7.5, "rate_limit_burst": 13}},
    )

    a = r.get_default_limiter()
    b = r.get_default_limiter()
    assert a is b
    assert a.rate == 7.5
    assert a.capacity == 13
    r.reset_default_for_tests()
