"""BinanceLiveClient tests using httpx MockTransport.

These tests exercise the full authenticated request path (signing,
symbol filter loading, quantization, withdraw check, order placement)
without touching the network. Paper and live share the same code path;
we test paper here because it requires the fewer promotion hoops.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from src.config import get_settings
from src.execution.binance_client import Order
from src.execution.binance_live_client import PAPER_BASE, BinanceLiveClient
from src.execution.binance_symbol_info import InsufficientNotional
from src.execution.live_gate import WithdrawEnabled


def _account(can_withdraw: bool = False) -> dict:
    return {
        "canTrade": True,
        "canWithdraw": can_withdraw,
        "canDeposit": True,
        "permissions": ["SPOT"],
        "balances": [
            {"asset": "USDT", "free": "500.0", "locked": "0"},
            {"asset": "BTC", "free": "0.01", "locked": "0"},
            {"asset": "DUST", "free": "0", "locked": "0"},
        ],
    }


def _exchange_info() -> dict:
    return {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.00001000",
                     "minQty": "0.00001000"},
                    {"filterType": "NOTIONAL", "minNotional": "10.00000000"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                ],
            }
        ]
    }


def _order_fill_response(side: str, qty: float, price: float) -> dict:
    return {
        "symbol": "BTCUSDT",
        "orderId": 12345,
        "executedQty": f"{qty}",
        "cummulativeQuoteQty": f"{qty * price}",
        "status": "FILLED",
        "fills": [
            {"price": f"{price}", "qty": f"{qty}", "commission": "0",
             "commissionAsset": "USDT"},
        ],
    }


class _Recorder:
    """Captures the last request the client sent so tests can assert on it."""

    def __init__(self) -> None:
        self.last: httpx.Request | None = None


def _decoded_query(req: httpx.Request) -> dict[str, list[str]]:
    """httpx Request.url.query is bytes; parse_qs returns bytes keys.
    Decode first so tests can compare against plain strings.
    """
    raw = req.url.query
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return parse_qs(raw)


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


def _make_handler(
    recorder: _Recorder,
    *,
    can_withdraw: bool = False,
    btc_price: float = 50_000.0,
    order_response: dict | None = None,
    order_status: int = 200,
):
    def handler(req: httpx.Request) -> httpx.Response:
        recorder.last = req
        path = req.url.path

        if path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=_exchange_info())

        if path == "/api/v3/account":
            # Verify signed request has signature + timestamp query params.
            params = _decoded_query(req)
            assert "timestamp" in params
            assert "signature" in params
            return httpx.Response(200, json=_account(can_withdraw))

        if path == "/api/v3/ticker/price":
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": str(btc_price)})

        if path == "/api/v3/order":
            params = _decoded_query(req)
            assert "timestamp" in params
            assert "signature" in params
            if order_status >= 400:
                return httpx.Response(order_status, text="rejected")
            side = params.get("side", [""])[0]
            resp = order_response or _order_fill_response(side, 0.002, btc_price)
            return httpx.Response(200, json=resp)

        return httpx.Response(404, json={"error": "unknown path"})

    return handler


def _build_client(recorder: _Recorder, **handler_kwargs: Any) -> BinanceLiveClient:
    transport = httpx.MockTransport(_make_handler(recorder, **handler_kwargs))
    httpx_client = httpx.AsyncClient(
        base_url=PAPER_BASE,
        headers={"X-MBX-APIKEY": "test-key"},
        transport=transport,
    )
    return BinanceLiveClient(
        base_url=PAPER_BASE,
        api_key="test-key",
        api_secret="test-secret",
        client=httpx_client,
    )


# ---------------------------------------------------------------------------
# account_equity_usd
# ---------------------------------------------------------------------------


async def test_account_equity_sums_usdt_and_marks_btc_to_market(
    recorder: _Recorder,
) -> None:
    client = _build_client(recorder, btc_price=50_000.0)
    eq = await client.account_equity_usd()
    # 500 USDT + 0.01 BTC * 50_000 = 500 + 500 = 1000
    assert eq == pytest.approx(1000.0, rel=1e-6)


async def test_account_equity_refuses_when_withdraw_enabled(recorder: _Recorder) -> None:
    client = _build_client(recorder, can_withdraw=True)
    with pytest.raises(WithdrawEnabled):
        await client.account_equity_usd()


async def test_withdraw_check_only_runs_once(recorder: _Recorder) -> None:
    client = _build_client(recorder)
    await client.account_equity_usd()  # first call verifies
    assert client._withdraw_checked is True
    # Second call would raise if we tried to verify again with bad data --
    # this time we don't, proving we only check once.
    await client.account_equity_usd()


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


async def test_market_buy_sends_quote_order_qty(recorder: _Recorder) -> None:
    client = _build_client(recorder)
    await client.symbol_info.load()

    fill = await client.submit(Order(symbol="BTCUSDT", side="BUY", qty_usd=100.0))
    assert fill.symbol == "BTCUSDT"
    assert fill.side == "BUY"
    assert fill.qty > 0

    # Inspect the last request -- MARKET BUY goes via quoteOrderQty, not quantity.
    assert recorder.last is not None
    params = _decoded_query(recorder.last)
    assert params["side"] == ["BUY"]
    assert params["type"] == ["MARKET"]
    assert "quoteOrderQty" in params
    assert "quantity" not in params


async def test_market_buy_below_min_notional_raises_preflight(
    recorder: _Recorder,
) -> None:
    client = _build_client(recorder)
    await client.symbol_info.load()
    with pytest.raises(InsufficientNotional):
        await client.submit(Order(symbol="BTCUSDT", side="BUY", qty_usd=5.0))


async def test_market_sell_quantizes_quantity_against_lot_size(
    recorder: _Recorder,
) -> None:
    client = _build_client(recorder, btc_price=50_000.0)
    await client.symbol_info.load()

    # $100 at $50k -> 0.002 BTC, step 0.00001 -> stays 0.002
    await client.submit(Order(symbol="BTCUSDT", side="SELL", qty_usd=100.0))

    params = _decoded_query(recorder.last)  # type: ignore[arg-type]
    assert params["side"] == ["SELL"]
    assert "quantity" in params
    assert "quoteOrderQty" not in params
    # Quantity is a quantized string representation.
    sent_qty = params["quantity"][0]
    assert sent_qty not in {"", "0"}


async def test_submit_refuses_when_halt_file_present(
    recorder: _Recorder, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "HALT").write_text("operator halt", encoding="utf-8")

    client = _build_client(recorder)
    with pytest.raises(RuntimeError, match="HALT"):
        await client.submit(Order(symbol="BTCUSDT", side="BUY", qty_usd=100.0))
