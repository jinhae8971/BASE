"""SimulatedBinanceClient unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.backtest.portfolio_sim import SimConfig, SimulatedBinanceClient
from src.execution.binance_client import Order


@pytest.fixture
def sim() -> SimulatedBinanceClient:
    s = SimulatedBinanceClient(
        config=SimConfig(initial_cash=1000.0, fee_bps=10.0, slippage_bps=0.0)
    )
    s.advance(datetime(2024, 1, 1, tzinfo=UTC), {"BTCUSDT": 50_000.0, "ETHUSDT": 3_000.0})
    return s


async def test_buy_fills_reduce_cash_and_add_position(sim: SimulatedBinanceClient) -> None:
    fill = await sim.submit(Order(symbol="BTCUSDT", side="BUY", qty_usd=500.0))
    # 500 USD - 0.1% fee = 499.5 used to buy at 50000 => 0.00999 BTC
    assert fill.qty == pytest.approx(499.5 / 50_000.0, rel=1e-6)
    assert sim.state.cash == pytest.approx(500.0, rel=1e-6)
    assert sim.state.positions["BTCUSDT"] == pytest.approx(fill.qty)


async def test_sell_fills_add_cash_and_reduce_position(sim: SimulatedBinanceClient) -> None:
    await sim.submit(Order(symbol="BTCUSDT", side="BUY", qty_usd=500.0))
    qty_before = sim.state.positions["BTCUSDT"]
    await sim.submit(Order(symbol="BTCUSDT", side="SELL", qty_usd=250.0))
    assert sim.state.positions["BTCUSDT"] < qty_before
    assert sim.state.cash > 500.0


async def test_buy_clamps_to_available_cash(sim: SimulatedBinanceClient) -> None:
    # Request 10,000 USD of BTC with only 1,000 USD cash.
    fill = await sim.submit(Order(symbol="BTCUSDT", side="BUY", qty_usd=10_000.0))
    assert sim.state.cash == pytest.approx(0.0, abs=1e-6)
    assert fill.qty > 0


async def test_mark_equity_tracks_price_moves(sim: SimulatedBinanceClient) -> None:
    await sim.submit(Order(symbol="BTCUSDT", side="BUY", qty_usd=1000.0))
    eq_before = sim.mark_equity()
    sim.advance(datetime(2024, 1, 2, tzinfo=UTC), {"BTCUSDT": 100_000.0, "ETHUSDT": 3_000.0})
    eq_after = sim.mark_equity()
    assert eq_after > eq_before  # BTC doubled
