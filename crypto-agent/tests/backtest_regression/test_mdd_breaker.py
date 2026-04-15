"""MDD circuit-breaker test.

Previously the BacktestEngine re-anchored `peak_equity_usd` to the current
equity on every day, which meant `check_mdd` always saw zero drawdown and
the circuit breaker could never fire. Phase 8.5 moves the high-water mark
into `SimulatedBinanceClient` so the breaker sees the true running peak.

This test constructs a tiny backtest where:
  - BTC rips 20% up during warmup, then crashes 50% back down,
  - the executor keeps buying BTC on every ELO-weighted day,
  - a 15% MDD cap must therefore trip on the way down.

We assert that peak_equity_usd tracks the true peak across ticks, and that
`check_mdd` would return HALT_ALL once the drawdown breaches the cap.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.backtest.portfolio_sim import SimConfig, SimulatedBinanceClient
from src.execution.binance_client import Order
from src.portfolio.risk import GuardAction, PortfolioState, check_mdd


async def test_peak_equity_tracks_running_max_across_advances() -> None:
    sim = SimulatedBinanceClient(config=SimConfig(initial_cash=1000.0, fee_bps=0.0, slippage_bps=0.0))

    # Day 1: BTC at 100, buy $500.
    sim.advance(datetime(2024, 1, 1, tzinfo=UTC), {"BTCUSDT": 100.0})
    await sim.submit(Order(symbol="BTCUSDT", side="BUY", qty_usd=500.0))
    assert await sim.peak_equity_usd() == pytest.approx(1000.0)

    # Day 2: BTC pumps to 200. Portfolio mark = 500 cash + 5 BTC * 200 = 1500.
    sim.advance(datetime(2024, 1, 2, tzinfo=UTC), {"BTCUSDT": 200.0})
    peak_after_pump = await sim.peak_equity_usd()
    assert peak_after_pump == pytest.approx(1500.0)

    # Day 3: BTC crashes to 80. Portfolio = 500 + 5 * 80 = 900.
    # Peak must stay at 1500 (the running max), not drop to 900.
    sim.advance(datetime(2024, 1, 3, tzinfo=UTC), {"BTCUSDT": 80.0})
    peak_after_crash = await sim.peak_equity_usd()
    assert peak_after_crash == pytest.approx(1500.0)
    current = await sim.account_equity_usd()
    assert current == pytest.approx(900.0)


async def test_mdd_check_halts_when_drawdown_exceeds_cap_using_real_peak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a 40% drawdown from the running peak, the MDD guard should HALT."""
    monkeypatch.setenv("MDD_CIRCUIT_BREAKER_PCT", "15")
    from src.config import get_settings
    get_settings.cache_clear()  # type: ignore[attr-defined]

    sim = SimulatedBinanceClient(config=SimConfig(initial_cash=1000.0, fee_bps=0.0, slippage_bps=0.0))

    # Ramp BTC up to drive peak equity higher.
    sim.advance(datetime(2024, 1, 1, tzinfo=UTC), {"BTCUSDT": 100.0})
    await sim.submit(Order(symbol="BTCUSDT", side="BUY", qty_usd=1000.0))
    sim.advance(datetime(2024, 1, 2, tzinfo=UTC), {"BTCUSDT": 200.0})
    peak = await sim.peak_equity_usd()
    assert peak == pytest.approx(2000.0)

    # Crash below the 15% cap.
    sim.advance(datetime(2024, 1, 3, tzinfo=UTC), {"BTCUSDT": 100.0})
    current = await sim.account_equity_usd()
    state = PortfolioState(
        equity_usd=current,
        peak_equity_usd=peak,
        daily_pnl_pct=0.0,
        weekly_pnl_pct=0.0,
    )
    decision = check_mdd(state)
    assert decision.action == GuardAction.HALT_ALL, decision.reason
    assert "MDD" in decision.reason


async def test_mdd_check_allows_within_cap_using_real_peak() -> None:
    """A 10% drawdown from peak stays under the 15% cap."""
    sim = SimulatedBinanceClient(config=SimConfig(initial_cash=1000.0, fee_bps=0.0, slippage_bps=0.0))

    sim.advance(datetime(2024, 1, 1, tzinfo=UTC), {"BTCUSDT": 100.0})
    await sim.submit(Order(symbol="BTCUSDT", side="BUY", qty_usd=1000.0))
    sim.advance(datetime(2024, 1, 2, tzinfo=UTC), {"BTCUSDT": 120.0})
    # Mild pullback.
    sim.advance(datetime(2024, 1, 3, tzinfo=UTC), {"BTCUSDT": 108.0})

    state = PortfolioState(
        equity_usd=await sim.account_equity_usd(),
        peak_equity_usd=await sim.peak_equity_usd(),
        daily_pnl_pct=0.0,
        weekly_pnl_pct=0.0,
    )
    decision = check_mdd(state)
    assert decision.action == GuardAction.ALLOW
