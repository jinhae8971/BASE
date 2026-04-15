"""Shared async HTTP client with retries and per-host rate limiting.

All data sources funnel through this module so policy (timeouts, retries,
headers, logging) lives in one place.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.logging import get_logger

log = get_logger("http")

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
USER_AGENT = "crypto-agent/0.0.1 (+https://github.com/jinhae8971/base)"


@dataclass
class RateLimit:
    """Token-bucket-ish limiter: max `requests` per `per_seconds` window.

    Sliding window implemented with a deque of timestamps. Cheap enough for
    the sub-100 req/min rates we care about.
    """

    requests: int
    per_seconds: float


class _HostLimiter:
    def __init__(self) -> None:
        self._limits: dict[str, RateLimit] = {}
        self._history: dict[str, deque[float]] = defaultdict(deque)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def configure(self, host: str, limit: RateLimit) -> None:
        self._limits[host] = limit

    async def acquire(self, host: str) -> None:
        limit = self._limits.get(host)
        if limit is None:
            return
        async with self._locks[host]:
            now = asyncio.get_event_loop().time()
            hist = self._history[host]
            cutoff = now - limit.per_seconds
            while hist and hist[0] < cutoff:
                hist.popleft()
            if len(hist) >= limit.requests:
                wait = limit.per_seconds - (now - hist[0])
                if wait > 0:
                    log.debug("http.rate_wait", host=host, wait=wait)
                    await asyncio.sleep(wait)
            hist.append(asyncio.get_event_loop().time())


_limiter = _HostLimiter()


def configure_rate_limit(host: str, requests: int, per_seconds: float) -> None:
    _limiter.configure(host, RateLimit(requests, per_seconds))


def build_client(
    base_url: str = "",
    headers: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        merged.update(headers)
    return httpx.AsyncClient(
        base_url=base_url,
        headers=merged,
        timeout=DEFAULT_TIMEOUT,
        transport=transport,
        follow_redirects=True,
    )


async def get_json(
    client: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    host: str | None = None,
    retries: int = 3,
) -> Any:
    """GET with retry on 5xx/network + per-host rate limiting.

    Returns parsed JSON. 4xx errors are raised immediately (non-retryable).
    """
    host_key = host or (client.base_url.host if client.base_url else "default")
    await _limiter.acquire(host_key)

    try:
        async for attempt in AsyncRetrying(
            reraise=True,
            stop=stop_after_attempt(retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type((httpx.TransportError, _ServerError)),
        ):
            with attempt:
                resp = await client.get(path, params=params)
                if resp.status_code >= 500:
                    raise _ServerError(f"{resp.status_code} on {path}")
                resp.raise_for_status()
                return resp.json()
    except RetryError as exc:  # pragma: no cover - tenacity reraise covers this
        raise exc.last_attempt.exception()  # type: ignore[misc]

    raise RuntimeError("unreachable")


class _ServerError(Exception):
    """Retryable: upstream 5xx."""
