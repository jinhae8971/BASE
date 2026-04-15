"""End-to-end backtest test using the HeuristicLLMClient.

Wires HistoricalSnapshotProvider -> BacktestEngine -> DailyWorkflow ->
HeuristicLLMClient over synthetic candles, and asserts the engine completes
and produces a valid PerformanceReport.

Two scenarios:
  1. **Rising BTC**: BTC climbs, the engine should end with > 0 equity and
     the BTC benchmark should be > initial.
  2. **Flat market**: everything flat, engine should end at ~initial equity
     and MDD should be ~0.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.heuristic_llm import HeuristicLLMClient
from src.backtest.portfolio_sim import SimConfig
from src.backtest.provider import HistoricalSnapshotProvider
from src.data.binance_md import Candle


def _gen_candles(
    n_days: int,
    start_price: float = 20_000.0,
    daily_drift: float = 0.002,
) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    candles: list[Candle] = []
    price = start_price
    for i in range(n_days):
        day = start + timedelta(days=i)
        price = price * (1 + daily_drift)
        candles.append(
            Candle(
                open_time=day,
                open=price * 0.998,
                high=price * 1.005,
                low=price * 0.995,
                close=price,
                volume=1_000,
                quote_volume=price * 1_000,
            )
        )
    return candles


def _provider(drift_btc: float, drift_eth: float, n_days: int = 120) -> HistoricalSnapshotProvider:
    return HistoricalSnapshotProvider(
        universe=["BTCUSDT", "ETHUSDT"],
        candles_by_symbol={
            "BTCUSDT": _gen_candles(n_days, 30_000.0, drift_btc),
            "ETHUSDT": _gen_candles(n_days, 2_000.0, drift_eth),
        },
        macro_by_day={},  # empty -> heuristic falls back to neutral regime
        warmup_candles=25,
    )


async def test_backtest_runs_on_rising_trend_and_produces_report() -> None:
    engine = BacktestEngine(
        provider=_provider(drift_btc=0.003, drift_eth=0.003, n_days=120),
        llm_client=HeuristicLLMClient(),
        sim_config=SimConfig(initial_cash=1000.0),
    )
    result = await engine.run()

    assert len(result.days) > 0
    assert len(result.equity) == len(result.days)
    assert result.report.btc_total_return_pct > 0  # BTC benchmark went up
    # Engine executed at least one rebalance day.
    assert result.num_orders >= 1
    # Strategy return is a real number and MDD is non-negative.
    assert isinstance(result.report.total_return_pct, float)
    assert result.report.mdd_pct >= 0.0
    # Report summary string has all key metric tokens.
    summary = result.report.summary()
    assert "Return" in summary
    assert "MDD" in summary
    assert "BTC" in summary


async def test_backtest_flat_market_has_tiny_drawdown() -> None:
    engine = BacktestEngine(
        provider=_provider(drift_btc=0.0, drift_eth=0.0, n_days=60),
        llm_client=HeuristicLLMClient(),
        sim_config=SimConfig(initial_cash=1000.0, fee_bps=0.0, slippage_bps=0.0),
    )
    result = await engine.run()

    # With zero drift, zero fees, zero slippage, the BTC benchmark is exactly flat.
    assert result.report.btc_total_return_pct == pytest.approx(0.0, abs=1e-9)
    # Strategy may sit in cash or buy-and-hold; drawdown stays tiny.
    assert result.report.mdd_pct < 2.0
