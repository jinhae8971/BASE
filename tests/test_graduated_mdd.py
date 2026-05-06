from __future__ import annotations

from datetime import date

import pandas as pd

from common.types import PortfolioTarget
from portfolio.risk_guards import DailyRiskGuard


def _target() -> PortfolioTarget:
    return PortfolioTarget(
        as_of=date(2025, 5, 6),
        cash_weight=0.1,
        positions={"005930": 0.5, "000660": 0.4},
    )


def _eq_with_drawdown(dd_pct: float) -> pd.Series:
    return pd.Series(
        [100, 105, 110, 110 * (1 + dd_pct)],
        index=pd.date_range("2025-01-01", periods=4),
    )


def _patch_settings(monkeypatch, payload: dict) -> None:
    from common import config as c

    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr("common.config.load_yaml_settings", lambda: payload)


def _safe_settings() -> dict:
    return {
        "risk": {
            "graduated_mdd": [0.10, 0.12, 0.15],
            "daily_loss_kill": 0.99,
            "max_portfolio_mdd": 0.99,
            "max_turnover_daily": 0.99,
            "cash_buffer_min": 0.05,
        }
    }


def test_mdd_minus10_trims_25pct(monkeypatch) -> None:
    _patch_settings(monkeypatch, _safe_settings())
    eq = _eq_with_drawdown(-0.105)
    g = DailyRiskGuard(prev_nav=eq.iloc[-1], equity_curve=eq)
    decision = g.evaluate(_target(), nav_today=eq.iloc[-1], new_orders_notional=0)
    assert decision.target_override is not None
    assert abs(decision.target_override.positions["005930"] - 0.375) < 1e-9
    assert abs(decision.target_override.positions["000660"] - 0.30) < 1e-9


def test_mdd_minus15_trims_75pct(monkeypatch) -> None:
    _patch_settings(monkeypatch, _safe_settings())
    eq = _eq_with_drawdown(-0.155)
    g = DailyRiskGuard(prev_nav=eq.iloc[-1], equity_curve=eq)
    decision = g.evaluate(_target(), nav_today=eq.iloc[-1], new_orders_notional=0)
    assert decision.target_override is not None
    assert abs(decision.target_override.positions["005930"] - 0.125) < 1e-9
    assert abs(decision.target_override.positions["000660"] - 0.10) < 1e-9


def test_mdd_below_first_tier_no_override(monkeypatch) -> None:
    _patch_settings(monkeypatch, _safe_settings())
    eq = _eq_with_drawdown(-0.05)
    g = DailyRiskGuard(prev_nav=eq.iloc[-1], equity_curve=eq)
    decision = g.evaluate(_target(), nav_today=eq.iloc[-1], new_orders_notional=0)
    assert decision.target_override is None
    assert decision.allow is True
