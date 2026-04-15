"""BinanceSymbolInfo quantization + filter tests."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from src.execution.binance_symbol_info import (
    BinanceSymbolInfo,
    InsufficientNotional,
    SymbolNotSupported,
    _floor_to_step,
)


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
            },
            {
                "symbol": "DOGEUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "1",
                     "minQty": "1"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.00001"},
                ],
            },
        ]
    }


@pytest.fixture
async def info() -> BinanceSymbolInfo:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v3/exchangeInfo"
        return httpx.Response(200, json=_exchange_info())

    client = httpx.AsyncClient(
        base_url="https://testnet.binance.vision",
        transport=httpx.MockTransport(handler),
    )
    si = BinanceSymbolInfo("https://testnet.binance.vision", client=client)
    await si.load()
    return si


async def test_quantize_buy_floors_to_step_and_meets_min_notional(info: BinanceSymbolInfo) -> None:
    # $100 at $50,000/BTC -> 0.002 BTC -> step 0.00001 is fine
    qty = info.quantize_buy("BTCUSDT", qty_usd=100.0, price=50_000.0)
    assert qty == pytest.approx(0.002, abs=1e-9)


async def test_quantize_buy_rounds_down_to_step(info: BinanceSymbolInfo) -> None:
    # 123 DOGE at $0.10 -> $12.3. Step is 1 DOGE, so it stays at 123.
    qty = info.quantize_buy("DOGEUSDT", qty_usd=12.3, price=0.10)
    # 12.3 / 0.10 = 123 exactly; stepSize 1 -> 123
    assert qty == 123.0


async def test_quantize_buy_raises_below_min_notional(info: BinanceSymbolInfo) -> None:
    # $5 of BTC at $50,000 = 0.0001 BTC. Step 0.00001 allows it, but
    # notional is $5 < minNotional $10 -> raise.
    with pytest.raises(InsufficientNotional):
        info.quantize_buy("BTCUSDT", qty_usd=5.0, price=50_000.0)


async def test_quantize_sell_rounds_to_step_and_enforces_min_qty(info: BinanceSymbolInfo) -> None:
    # Hold 1.234567 BTC, step 0.00001 -> 1.23456 (floored)
    qty = info.quantize_sell("BTCUSDT", qty=1.234567)
    assert qty == pytest.approx(1.23456, abs=1e-9)


async def test_quantize_sell_below_min_qty_raises(info: BinanceSymbolInfo) -> None:
    # DOGE minQty=1, attempting to sell 0.5 rounds to 0, below minQty.
    with pytest.raises(InsufficientNotional):
        info.quantize_sell("DOGEUSDT", qty=0.5)


async def test_unknown_symbol_raises(info: BinanceSymbolInfo) -> None:
    with pytest.raises(SymbolNotSupported):
        info.quantize_sell("NOTREALUSDT", qty=1.0)


def test_floor_to_step_pure_decimal() -> None:
    assert _floor_to_step(Decimal("1.234567"), Decimal("0.01")) == Decimal("1.23")
    assert _floor_to_step(Decimal("100"), Decimal("1")) == Decimal("100")
    assert _floor_to_step(Decimal("0.999"), Decimal("0.001")) == Decimal("0.999")


async def test_format_qty_strips_trailing_zeros(info: BinanceSymbolInfo) -> None:
    s = info.format_qty("BTCUSDT", 1.5)
    assert "e" not in s.lower()
    assert s in {"1.5", "1.50", "1.50000"}  # trailing zeros acceptable but no exponent
