"""Verify the 'let winners run' asymmetric rebalance threshold."""
from __future__ import annotations

from datetime import date

from agents.execution_agent import ExecutionAgent
from common.types import PortfolioTarget, Side


def _patch_settings(monkeypatch, payload: dict) -> None:
    from common import config as c

    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr("common.config.load_yaml_settings", lambda: payload)


def test_buy_drift_4pct_fires(monkeypatch) -> None:
    _patch_settings(
        monkeypatch,
        {
            "execution": {
                "rebalance_buy_threshold": 0.04,
                "rebalance_sell_threshold": 0.08,
                "twap_enabled": False,
            },
            "risk": {"max_position_weight": 0.10},
        },
    )
    target = PortfolioTarget(
        as_of=date(2025, 5, 6),
        cash_weight=0.0,
        positions={"005930": 0.5},
    )
    current = {"005930": 460}
    cash = 67_800_000
    prices = {"005930": 70_000.0}
    orders = ExecutionAgent()._plan_orders(target, current, cash, prices)
    assert any(o.side is Side.BUY and o.ticker == "005930" for o in orders)


def test_sell_drift_below_8pct_does_not_fire(monkeypatch) -> None:
    _patch_settings(
        monkeypatch,
        {
            "execution": {
                "rebalance_buy_threshold": 0.04,
                "rebalance_sell_threshold": 0.08,
                "twap_enabled": False,
            },
            "risk": {"max_position_weight": 0.20},
        },
    )
    target = PortfolioTarget(
        as_of=date(2025, 5, 6),
        cash_weight=0.5,
        positions={"005930": 0.5},
    )
    current = {"005930": 800}
    cash = 44_000_000
    prices = {"005930": 70_000.0}
    orders = ExecutionAgent()._plan_orders(target, current, cash, prices)
    sells = [o for o in orders if o.side is Side.SELL]
    assert sells == []


def test_target_zero_always_sells_full(monkeypatch) -> None:
    """A target weight of zero means 'exit' — must sell regardless of drift."""
    _patch_settings(
        monkeypatch,
        {
            "execution": {
                "rebalance_buy_threshold": 0.04,
                "rebalance_sell_threshold": 0.08,
            },
            "risk": {"max_position_weight": 0.10},
        },
    )
    target = PortfolioTarget(
        as_of=date(2025, 5, 6),
        cash_weight=1.0,
        positions={},
    )
    current = {"005930": 100}
    cash = 93_000_000
    prices = {"005930": 70_000.0}
    orders = ExecutionAgent()._plan_orders(target, current, cash, prices)
    sells = [o for o in orders if o.side is Side.SELL]
    assert len(sells) == 1
    assert sells[0].quantity == 100
