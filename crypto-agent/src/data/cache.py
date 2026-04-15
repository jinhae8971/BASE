"""Tiny async TTL cache.

Backed by an in-process dict for tests and dry mode. Phase 2 can transparently
swap in Redis (same async API). Keys are strings; values must be JSON-
serializable so that the Redis upgrade is a drop-in replacement.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _Entry:
    expires_at: float
    value: Any


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._store[key]
                return None
            return entry.value

    async def set(self, key: str, value: Any, ttl: int) -> None:
        # Validate serializability now so bugs surface in the call site, not
        # when we eventually switch to Redis.
        json.dumps(value, default=str)
        async with self._lock:
            self._store[key] = _Entry(
                expires_at=time.monotonic() + ttl, value=value
            )

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()


_default = TTLCache()


def default_cache() -> TTLCache:
    return _default


async def cached(
    key: str,
    ttl: int,
    loader,  # async callable () -> value
    cache: TTLCache | None = None,
) -> Any:
    c = cache or _default
    hit = await c.get(key)
    if hit is not None:
        return hit
    value = await loader()
    await c.set(key, value, ttl)
    return value
