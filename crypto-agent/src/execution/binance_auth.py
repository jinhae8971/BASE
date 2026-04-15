"""HMAC-SHA256 request signing for Binance spot REST endpoints.

All private Binance endpoints require:
  - `X-MBX-APIKEY` header carrying the API key,
  - a `timestamp` query parameter (ms since epoch),
  - a `signature` query parameter = `HMAC_SHA256(query_string, secret)`.

We also add `recvWindow` to tolerate small clock drift without accepting
stale requests. This module is stateless and dependency-free so it's
trivially unit-testable — every test pins a fixed `timestamp_ms` and
asserts the resulting hex matches a known vector.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

DEFAULT_RECV_WINDOW_MS = 5000


def now_ms() -> int:
    return int(time.time() * 1000)


def sign(query_string: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_signed_query(
    params: dict[str, Any],
    secret: str,
    *,
    timestamp_ms: int | None = None,
    recv_window_ms: int = DEFAULT_RECV_WINDOW_MS,
) -> str:
    """Build a URL-encoded query string with timestamp + signature appended.

    Notes:
      - `None` values are dropped. Binance rejects empty strings for
        numeric params, so do not pass them.
      - Insertion order is preserved in the resulting query string. Since
        the signature is over the exact bytes we send, the client must
        send the *same* query string it signed.
    """
    cleaned: dict[str, Any] = {k: v for k, v in params.items() if v is not None}
    cleaned["timestamp"] = timestamp_ms if timestamp_ms is not None else now_ms()
    cleaned["recvWindow"] = recv_window_ms
    qs = urlencode(cleaned)
    signature = sign(qs, secret)
    return f"{qs}&signature={signature}"
