from __future__ import annotations

from datetime import date

import pandas as pd

from common.types import Order, PortfolioTarget, Side
from portfolio.risk_guards import DailyRiskGuard


def _target() -> PortfolioTarget:
    return PortfolioTarget(
        as_of=date(2025, 5, 6),
        cash_weight=0.1,
        positions={"005930": 0.5, "000660": 0.4},
    )


def test_daily_loss_kill_blocks_orders() -> None:
    g = DailyRiskGuard(prev_nav=100_000_000)
    decision = g.evaluate(_target(), nav_today=96_000_000, new_orders_notional=10_000_000)
    assert decision.allow is False
    assert "daily_loss_kill" in decision.triggered[0]


def test_mdd_trigger_de_levers() -> None:
    # Equity peaks at 110, drops to 88 = -20% drawdown — top tier (75% trim)
    eq = pd.Series([100, 105, 110, 95, 88], index=pd.date_range("2025-01-01", periods=5))
    g = DailyRiskGuard(prev_nav=88, equity_curve=eq)
    decision = g.evaluate(_target(), nav_today=88, new_orders_notional=10_000_000)
    assert decision.allow is True
    assert decision.target_override is not None
    trimmed = decision.target_override.positions
    # 0.5 * 0.25 = 0.125, 0.4 * 0.25 = 0.10
    assert abs(trimmed["005930"] - 0.125) < 1e-9
    assert abs(trimmed["000660"] - 0.10) < 1e-9


def test_turnover_cap_scales_down() -> None:
    g = DailyRiskGuard(prev_nav=100, executed_today_notional=20)
    # max_turnover default 0.30 → 30 of 100; already 20, new 30 would total 50%
    decision = g.evaluate(_target(), nav_today=100, new_orders_notional=30)
    assert decision.allow is True
    assert decision.scale < 1.0
    # Apply to orders
    orders = [Order(ticker="005930", side=Side.BUY, quantity=100, price=70_000)]
    cut = g.apply_to_orders(orders, decision)
    assert cut[0].quantity < 100


def test_no_trigger_when_within_limits() -> None:
    g = DailyRiskGuard(prev_nav=100_000_000, executed_today_notional=0)
    decision = g.evaluate(_target(), nav_today=99_500_000, new_orders_notional=5_000_000)
    assert decision.allow is True
    assert decision.scale == 1.0
    assert decision.target_override is None
