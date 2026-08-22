"""Upbit Open API client (https://docs.upbit.com).

Covers exactly what the day-trading engine needs:

* Public quotation — market list, tickers, day/minute candles, orderbook, ticks
* Private exchange — accounts, order chance, place/cancel/inspect orders

Auth is a JWT (HS256) over ``{access_key, nonce}``, plus ``query_hash`` when the
request carries parameters. Credentials come from :mod:`upbit.credentials`,
i.e. from what the user typed into the dashboard.

Rate limits are enforced client-side with a small token bucket per group
(quotation and exchange are metered separately by Upbit).
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid as uuid_mod
from collections import deque
from datetime import datetime
from typing import Any, Literal
from urllib.parse import unquote, urlencode

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from common.config import get_setting
from common.logging import get_logger

from . import credentials as creds_mod
from .types import OrderRequest, OrderResult

log = get_logger(__name__)

DEFAULT_BASE_URL = "https://api.upbit.com"

# Upbit publishes 10 req/s for quotation and 8 req/s for exchange endpoints.
# Stay a little under so a burst never trips a 429.
_RATE_LIMITS = {"quotation": (8, 1.0), "exchange": (6, 1.0), "order": (6, 1.0)}


class UpbitAPIError(RuntimeError):
    """Non-retryable error returned by the Upbit API."""

    def __init__(self, message: str, *, status: int | None = None, name: str | None = None):
        super().__init__(message)
        self.status = status
        self.name = name


class UpbitRateLimitError(RuntimeError):
    """HTTP 429 — retried with backoff."""


class _TokenBucket:
    """Simple sliding-window limiter shared across threads."""

    def __init__(self, capacity: int, window: float) -> None:
        self.capacity = capacity
        self.window = window
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._hits and now - self._hits[0] >= self.window:
                    self._hits.popleft()
                if len(self._hits) < self.capacity:
                    self._hits.append(now)
                    return
                sleep_for = self.window - (now - self._hits[0])
            time.sleep(max(sleep_for, 0.01))


_BUCKETS = {name: _TokenBucket(cap, win) for name, (cap, win) in _RATE_LIMITS.items()}


class UpbitClient:
    """Thin, synchronous Upbit wrapper.

    Public endpoints work without credentials; private ones raise
    :class:`upbit.credentials.CredentialsError` when no key pair is registered.
    """

    def __init__(
        self,
        *,
        access_key: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = (base_url or get_setting("upbit.base_url", DEFAULT_BASE_URL)).rstrip("/")
        self.timeout = timeout
        self._explicit = (access_key, secret_key) if access_key and secret_key else None
        self._client = httpx.Client(timeout=timeout, headers={"Accept": "application/json"})

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> UpbitClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _credentials(self) -> tuple[str, str]:
        if self._explicit:
            return self._explicit
        c = creds_mod.require()
        return c.access_key, c.secret_key

    def has_credentials(self) -> bool:
        try:
            self._credentials()
        except Exception:  # "no keys yet" is a normal state
            return False
        return True

    def _auth_header(self, params: dict[str, Any] | None = None) -> dict[str, str]:
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise UpbitAPIError(
                "PyJWT 패키지가 필요합니다. `pip install -e \".[upbit]\"` 로 설치하세요."
            ) from exc

        access_key, secret_key = self._credentials()
        payload: dict[str, Any] = {"access_key": access_key, "nonce": str(uuid_mod.uuid4())}
        if params:
            query = unquote(urlencode(params, doseq=True))
            payload["query_hash"] = hashlib.sha512(query.encode("utf-8")).hexdigest()
            payload["query_hash_alg"] = "SHA512"
        token = jwt.encode(payload, secret_key, algorithm="HS256")
        if isinstance(token, bytes):  # PyJWT < 2 compatibility
            token = token.decode("utf-8")
        return {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(min=1, max=8),
        retry=retry_if_exception_type((UpbitRateLimitError, httpx.TransportError)),
        reraise=True,
    )
    def _request(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth: bool = False,
        group: str = "quotation",
    ) -> Any:
        _BUCKETS[group].acquire()
        url = f"{self.base_url}{path}"
        headers = self._auth_header(params) if auth else {}

        if method == "GET":
            resp = self._client.get(url, params=params, headers=headers)
        elif method == "POST":
            resp = self._client.post(url, json=params, headers=headers)
        else:
            resp = self._client.delete(url, params=params, headers=headers)

        if resp.status_code == 429:
            raise UpbitRateLimitError(f"rate limited on {path}")
        if resp.status_code >= 400:
            name, message = _parse_error(resp)
            log.error(
                "upbit.api_error", path=path, status=resp.status_code, name=name, message=message
            )
            raise UpbitAPIError(message, status=resp.status_code, name=name)
        return resp.json()

    # ------------------------------------------------------------------
    # Quotation (public)
    # ------------------------------------------------------------------
    def get_markets(self, *, is_details: bool = True) -> list[dict[str, Any]]:
        """All tradable markets, including 유의/주의 flags when ``is_details``."""
        return self._request(
            "GET", "/v1/market/all", params={"isDetails": str(is_details).lower()}
        )

    def get_tickers(self, markets: list[str]) -> list[dict[str, Any]]:
        """Latest snapshot for up to ~100 markets per call; batched automatically."""
        out: list[dict[str, Any]] = []
        for i in range(0, len(markets), 100):
            chunk = markets[i : i + 100]
            out.extend(self._request("GET", "/v1/ticker", params={"markets": ",".join(chunk)}))
        return out

    def get_day_candles(self, market: str, count: int = 200) -> list[dict[str, Any]]:
        """Daily candles, newest first (Upbit caps ``count`` at 200)."""
        return self._request(
            "GET", "/v1/candles/days", params={"market": market, "count": min(count, 200)}
        )

    def get_minute_candles(
        self, market: str, unit: int = 60, count: int = 200
    ) -> list[dict[str, Any]]:
        """Minute candles, newest first. ``unit`` ∈ {1,3,5,10,15,30,60,240}."""
        return self._request(
            "GET",
            f"/v1/candles/minutes/{unit}",
            params={"market": market, "count": min(count, 200)},
        )

    def get_orderbook(self, markets: list[str]) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/orderbook", params={"markets": ",".join(markets)})

    def get_trade_ticks(self, market: str, count: int = 200) -> list[dict[str, Any]]:
        """Recent executions; ``ask_bid`` tells us whether the taker bought or sold."""
        return self._request(
            "GET", "/v1/trades/ticks", params={"market": market, "count": min(count, 500)}
        )

    # ------------------------------------------------------------------
    # Exchange (private)
    # ------------------------------------------------------------------
    def get_accounts(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/accounts", auth=True, group="exchange")

    def get_order_chance(self, market: str) -> dict[str, Any]:
        return self._request(
            "GET", "/v1/orders/chance", params={"market": market}, auth=True, group="exchange"
        )

    def get_order(self, order_uuid: str) -> dict[str, Any]:
        return self._request(
            "GET", "/v1/order", params={"uuid": order_uuid}, auth=True, group="exchange"
        )

    def cancel_order(self, order_uuid: str) -> dict[str, Any]:
        return self._request(
            "DELETE", "/v1/order", params={"uuid": order_uuid}, auth=True, group="order"
        )

    def place_order(self, req: OrderRequest) -> OrderResult:
        """Submit an order. Never called unless the engine is in ``live`` mode."""
        params: dict[str, Any] = {"market": req.market, "side": req.side, "ord_type": req.ord_type}
        if req.volume is not None:
            params["volume"] = _fmt_number(req.volume)
        if req.price is not None:
            params["price"] = _fmt_number(req.price)

        try:
            raw = self._request("POST", "/v1/orders", params=params, auth=True, group="order")
        except Exception as exc:  # surfaced to the dashboard, never fatal
            log.error("upbit.order_failed", market=req.market, side=req.side, error=str(exc))
            return OrderResult(
                request=req, state="rejected", message=str(exc), submitted_at=datetime.utcnow()
            )

        return OrderResult(
            request=req,
            uuid=raw.get("uuid"),
            state="submitted",
            executed_volume=float(raw.get("executed_volume") or 0.0),
            paid_fee=float(raw.get("paid_fee") or 0.0),
            submitted_at=datetime.utcnow(),
            raw=raw,
        )

    def wait_for_fill(self, order_uuid: str, *, timeout: float = 20.0, poll: float = 1.0) -> dict:
        """Poll an order until it leaves the ``wait`` state or ``timeout`` elapses."""
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.get_order(order_uuid)
            if last.get("state") in {"done", "cancel"}:
                return last
            time.sleep(poll)
        return last


def _parse_error(resp: httpx.Response) -> tuple[str | None, str]:
    try:
        body = resp.json()
    except Exception:  # HTML error pages, gateway timeouts
        return None, f"HTTP {resp.status_code}: {resp.text[:200]}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return err.get("name"), err.get("message") or f"HTTP {resp.status_code}"
    return None, f"HTTP {resp.status_code}: {str(body)[:200]}"


def _fmt_number(value: float) -> str:
    """Upbit wants plain decimal strings — never scientific notation."""
    return f"{value:.8f}".rstrip("0").rstrip(".") or "0"


# ----------------------------------------------------------------------
# KRW price ticks — used when `strategy.order_style` is `limit`.
# Table per Upbit's KRW-market 호가 단위 (2023-09 revision).
# ----------------------------------------------------------------------
_KRW_TICKS: tuple[tuple[float, float], ...] = (
    (2_000_000, 1000),
    (1_000_000, 1000),
    (500_000, 500),
    (100_000, 100),
    (10_000, 10),
    (1_000, 1),
    (100, 0.1),
    (10, 0.01),
    (1, 0.001),
    (0.1, 0.0001),
    (0.01, 0.00001),
    (0.001, 0.000001),
    (0.0001, 0.0000001),
)


def krw_tick_size(price: float) -> float:
    for threshold, tick in _KRW_TICKS:
        if price >= threshold:
            return tick
    return 0.00000001


def round_to_tick(price: float, *, up: bool = False) -> float:
    """Snap a price onto the KRW orderbook grid."""
    tick = krw_tick_size(price)
    steps = price / tick
    steps = int(steps) + 1 if up and steps % 1 else round(steps)
    return round(steps * tick, 8)
