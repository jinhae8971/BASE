"""Metrics unit tests -- known inputs, hand-computable outputs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.backtest.metrics import (
    compute_metrics,
    daily_returns,
    max_drawdown,
)


def test_daily_returns_simple() -> None:
    equity = [100, 110, 99]
    assert daily_returns(equity) == pytest.approx([0.10, -0.10], rel=1e-9)


def test_max_drawdown_picks_deepest_peak_to_trough() -> None:
    equity = [100, 120, 80, 90, 110]
    # Peak at 120, trough at 80 -> 33.333%
    assert max_drawdown(equity) == pytest.approx(1 / 3, rel=1e-9)


def test_compute_metrics_alpha_positive_when_strategy_beats_btc() -> None:
    days = [datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(10)]
    equity = [1000 + i * 20 for i in range(10)]       # +20/day linear
    btc = [1000 + i * 5 for i in range(10)]           # +5/day linear
    report = compute_metrics(days, equity, btc, gross_traded_usd=500)
    assert report.total_return_pct > report.btc_total_return_pct
    assert report.alpha_vs_btc_pp > 0
    assert report.mdd_pct == 0.0
    assert report.win_rate_pct == 100.0


def test_compute_metrics_flat_equity_gives_zero_sharpe() -> None:
    days = [datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(30)]
    equity = [1000.0] * 30
    btc = [1000.0] * 30
    report = compute_metrics(days, equity, btc)
    assert report.sharpe == 0.0
    assert report.total_return_pct == 0.0
    assert report.mdd_pct == 0.0
