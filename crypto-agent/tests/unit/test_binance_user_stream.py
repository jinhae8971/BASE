"""Binance userDataStream tests -- listenKey lifecycle + event parsing."""

from __future__ import annotations

import json

import httpx
import pytest

from src.execution.binance_user_stream import (
    BinanceUserStream,
    BinanceUserStreamRest,
    FakeWsTransport,
    parse_execution_report,
    report_to_fill,
)


# ---------------------------------------------------------------------------
# REST lifecycle
# ---------------------------------------------------------------------------


async def test_listen_key_create_keepalive_close() -> None:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.method == "POST":
            return httpx.Response(200, json={"listenKey": "abc123XYZ"})
        if req.method == "PUT":
            assert "listenKey=abc123XYZ" in req.url.query.decode()
            return httpx.Response(200, json={})
        if req.method == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    client = httpx.AsyncClient(
        base_url="https://testnet.binance.vision",
        transport=httpx.MockTransport(handler),
    )
    rest = BinanceUserStreamRest(client=client)

    key = await rest.create()
    assert key == "abc123XYZ"
    await rest.keepalive(key)
    await rest.close(key)

    assert [c.method for c in calls] == ["POST", "PUT", "DELETE"]


async def test_keepalive_raises_on_http_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(418, text="I'm a teapot")

    client = httpx.AsyncClient(
        base_url="https://testnet.binance.vision",
        transport=httpx.MockTransport(handler),
    )
    rest = BinanceUserStreamRest(client=client)
    with pytest.raises(httpx.HTTPStatusError):
        await rest.keepalive("k")


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------


def _execution_report(
    *,
    execution_type: str = "TRADE",
    order_status: str = "FILLED",
    side: str = "BUY",
    last_qty: float = 0.01,
    last_price: float = 50_000.0,
) -> dict:
    return {
        "e": "executionReport",
        "E": 1_700_000_000_000,
        "s": "BTCUSDT",
        "c": "client-1",
        "S": side,
        "o": "MARKET",
        "q": "0.01",
        "p": "0",
        "x": execution_type,
        "X": order_status,
        "i": 4242,
        "l": str(last_qty),
        "L": str(last_price),
        "z": str(last_qty),
        "n": "0.00001",
        "N": "BNB",
    }


def test_parse_execution_report_extracts_key_fields() -> None:
    report = parse_execution_report(_execution_report())
    assert report is not None
    assert report.symbol == "BTCUSDT"
    assert report.side == "BUY"
    assert report.execution_type == "TRADE"
    assert report.order_status == "FILLED"
    assert report.last_filled_qty == 0.01
    assert report.last_filled_price == 50_000.0


def test_parse_execution_report_ignores_other_event_types() -> None:
    assert parse_execution_report({"e": "outboundAccountPosition"}) is None


def test_report_to_fill_only_returns_on_trade_events() -> None:
    new_order = parse_execution_report(_execution_report(execution_type="NEW"))
    assert new_order is not None
    assert report_to_fill(new_order) is None

    trade = parse_execution_report(_execution_report())
    assert trade is not None
    fill = report_to_fill(trade)
    assert fill is not None
    assert fill.symbol == "BTCUSDT"
    assert fill.qty == 0.01
    assert fill.price == 50_000.0


def test_report_to_fill_handles_zero_qty_safely() -> None:
    raw = _execution_report(last_qty=0.0)
    report = parse_execution_report(raw)
    assert report is not None
    assert report_to_fill(report) is None


# ---------------------------------------------------------------------------
# End-to-end glue with FakeWsTransport
# ---------------------------------------------------------------------------


async def test_user_stream_yields_fills_from_fake_ws() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, json={"listenKey": "fakekey"})
        return httpx.Response(200, json={})

    rest_client = httpx.AsyncClient(
        base_url="https://testnet.binance.vision",
        transport=httpx.MockTransport(handler),
    )
    rest = BinanceUserStreamRest(client=rest_client)

    frames = [
        json.dumps({"e": "outboundAccountPosition"}),  # ignored
        json.dumps(_execution_report(side="BUY")),
        json.dumps(_execution_report(side="SELL", last_price=51_000.0)),
        "NOT_JSON",  # bad frame -> logged, skipped
    ]
    ws = FakeWsTransport(frames)

    async with BinanceUserStream(rest, ws) as stream:
        fills = []
        async for fill in stream.fills():
            fills.append(fill)

    assert len(ws.connected_urls) == 1
    assert "/ws/fakekey" in ws.connected_urls[0]
    assert len(fills) == 2
    assert {f.side for f in fills} == {"BUY", "SELL"}
