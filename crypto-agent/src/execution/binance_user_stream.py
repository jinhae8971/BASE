"""Binance spot userDataStream client.

Binance's spot user data stream is a WebSocket feed that pushes
execution reports (fills, rejections) and account balance updates in
real time. Setting one up is a 3-step dance:

  1. POST /api/v3/userDataStream       -> returns a `listenKey`
  2. WS connect to wss://<host>/ws/<listenKey>
  3. PUT  /api/v3/userDataStream?listenKey=... every 30 minutes to keep
     it alive. Binance auto-expires it after 60 minutes of silence.

This module splits the concerns cleanly so it's testable without a real
WebSocket library:

  - `BinanceUserStreamRest`   : the three REST calls above (listenKey
    lifecycle) backed by httpx. Fully testable with MockTransport.
  - `WsTransport`             : tiny Protocol -- `connect() -> async
    iterator of text frames`. Production uses the `websockets` package
    (lazy-imported); tests inject a fake that yields canned JSON.
  - `parse_execution_report`  : turns a raw ``executionReport`` event
    dict into a `Fill` compatible with our existing trade journal.
  - `BinanceUserStream`       : the glue -- given a REST client and a
    `WsTransport`, it manages the listenKey, runs a keep-alive task on
    a 30-minute cadence, and yields parsed Fills one at a time.

Integrating with `DailyWorkflow` / `PositionTracker` is left to the
operator (Phase 5.5): the stream is most useful during the paper-trade
shakedown where partial-fill patterns diverge from the REST-polling
assumptions baked into the backtest.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from src.execution.binance_client import Fill
from src.logging import get_logger

log = get_logger("user_stream")

LIVE_WS_HOST = "wss://stream.binance.com:9443"
PAPER_WS_HOST = "wss://stream.testnet.binance.vision"

KEEPALIVE_INTERVAL_S = 30 * 60  # 30 minutes


# ---------------------------------------------------------------------------
# REST lifecycle for the listenKey
# ---------------------------------------------------------------------------


class BinanceUserStreamRest:
    """Thin wrapper for the listenKey create/keepalive/close REST calls."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def create(self) -> str:
        resp = await self._client.post("/api/v3/userDataStream")
        resp.raise_for_status()
        return resp.json()["listenKey"]

    async def keepalive(self, listen_key: str) -> None:
        resp = await self._client.put(
            f"/api/v3/userDataStream?listenKey={listen_key}"
        )
        if resp.status_code >= 400:
            log.warning(
                "user_stream.keepalive_rejected",
                status=resp.status_code,
                body=resp.text[:200],
            )
            resp.raise_for_status()

    async def close(self, listen_key: str) -> None:
        try:
            resp = await self._client.delete(
                f"/api/v3/userDataStream?listenKey={listen_key}"
            )
            if resp.status_code >= 400:
                log.warning("user_stream.close_rejected", status=resp.status_code)
        except Exception as exc:  # noqa: BLE001
            log.warning("user_stream.close_failed", err=str(exc))


# ---------------------------------------------------------------------------
# WsTransport protocol + fake used in tests
# ---------------------------------------------------------------------------


class WsTransport(Protocol):
    """Minimal abstraction for a websocket frame source.

    Production wraps `websockets.connect(...)`; tests pass an in-memory
    iterator so we never need the real package installed.
    """

    async def connect(self, url: str) -> AsyncIterator[str]:  # pragma: no cover
        ...


class FakeWsTransport:
    """In-memory WS transport used by tests.

    Register a list of JSON strings once; each `connect()` yields them in
    order then returns.
    """

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.connected_urls: list[str] = []

    async def connect(self, url: str) -> AsyncIterator[str]:
        self.connected_urls.append(url)

        async def _iter() -> AsyncIterator[str]:
            for m in self.messages:
                yield m

        return _iter()


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------


@dataclass
class ExecutionReport:
    """Subset of fields we care about from an ``executionReport`` event."""

    symbol: str
    side: str              # BUY | SELL
    order_id: int
    client_order_id: str
    execution_type: str    # NEW | CANCELED | TRADE | REJECTED | ...
    order_status: str      # FILLED | PARTIALLY_FILLED | ...
    price: float
    quantity: float
    cumulative_filled_qty: float
    last_filled_qty: float
    last_filled_price: float
    commission: float
    commission_asset: str
    event_time: datetime


def parse_execution_report(msg: dict[str, Any]) -> ExecutionReport | None:
    """Return an `ExecutionReport` if the event is one we care about."""
    if msg.get("e") != "executionReport":
        return None
    return ExecutionReport(
        symbol=str(msg["s"]),
        side=str(msg["S"]),
        order_id=int(msg.get("i", 0)),
        client_order_id=str(msg.get("c", "")),
        execution_type=str(msg.get("x", "")),
        order_status=str(msg.get("X", "")),
        price=float(msg.get("p", 0) or 0),
        quantity=float(msg.get("q", 0) or 0),
        cumulative_filled_qty=float(msg.get("z", 0) or 0),
        last_filled_qty=float(msg.get("l", 0) or 0),
        last_filled_price=float(msg.get("L", 0) or 0),
        commission=float(msg.get("n", 0) or 0),
        commission_asset=str(msg.get("N", "") or ""),
        event_time=datetime.fromtimestamp(int(msg.get("E", 0)) / 1000, tz=UTC),
    )


def report_to_fill(report: ExecutionReport) -> Fill | None:
    """Turn a TRADE execution into our lightweight `Fill`, or None.

    Uses the ``last_filled_*`` fields exclusively -- those are the only
    ones guaranteed to represent actual execution. A TRADE event with
    zero last-filled qty/price is malformed and we drop it rather than
    silently fall back to the (possibly stale) total fields.
    """
    if report.execution_type != "TRADE":
        return None
    qty = report.last_filled_qty
    price = report.last_filled_price
    if qty <= 0 or price <= 0:
        return None
    return Fill(
        symbol=report.symbol,
        side=report.side,
        qty=qty,
        price=price,
        fee_usd=report.commission,  # in commissionAsset units; caller converts
        filled_at=report.event_time,
    )


# ---------------------------------------------------------------------------
# Glue: manage listenKey + keepalive + yield parsed Fills
# ---------------------------------------------------------------------------


class BinanceUserStream:
    def __init__(
        self,
        rest: BinanceUserStreamRest,
        ws_transport: WsTransport,
        ws_host: str = PAPER_WS_HOST,
    ) -> None:
        self._rest = rest
        self._ws = ws_transport
        self._ws_host = ws_host
        self._listen_key: str | None = None
        self._keepalive_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "BinanceUserStream":
        self._listen_key = await self._rest.create()
        log.info("user_stream.started", listen_key_prefix=self._listen_key[:6])
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
        if self._listen_key is not None:
            await self._rest.close(self._listen_key)

    async def fills(self) -> AsyncIterator[Fill]:
        assert self._listen_key is not None, "use `async with` to start the stream"
        url = f"{self._ws_host}/ws/{self._listen_key}"
        messages = await self._ws.connect(url)
        async for raw in messages:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("user_stream.bad_frame", body=raw[:200])
                continue
            report = parse_execution_report(msg)
            if report is None:
                continue
            fill = report_to_fill(report)
            if fill is not None:
                yield fill

    async def _keepalive_loop(self) -> None:
        assert self._listen_key is not None
        try:
            while True:
                await asyncio.sleep(KEEPALIVE_INTERVAL_S)
                try:
                    await self._rest.keepalive(self._listen_key)
                    log.debug("user_stream.keepalive_ok")
                except Exception as exc:  # noqa: BLE001
                    log.warning("user_stream.keepalive_failed", err=str(exc))
        except asyncio.CancelledError:
            raise
