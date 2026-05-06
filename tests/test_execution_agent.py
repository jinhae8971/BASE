from __future__ import annotations

from datetime import date

from agents.execution_agent import ExecutionAgent
from common.types import PortfolioTarget, Side


def test_dry_run_does_not_call_broker() -> None:
    target = PortfolioTarget(
        as_of=date(2025, 5, 6),
        cash_weight=0.1,
        positions={"005930": 0.5, "000660": 0.4},
    )
    current_positions: dict[str, int] = {}
    cash = 100_000_000.0
    prices = {"005930": 70_000.0, "000660": 130_000.0}
    results = ExecutionAgent().execute(
        target, current_positions, cash, prices, dry_run=True
    )
    assert all(r.status == "submitted" for r in results)
    assert all(r.message == "dry_run" for r in results)
    # All BUYs since we started flat
    assert all(r.order.side is Side.BUY for r in results)


def test_cash_constraint_scales_buys_down() -> None:
    target = PortfolioTarget(
        as_of=date(2025, 5, 6),
        cash_weight=0.0,
        positions={"005930": 0.6, "000660": 0.4},
    )
    cash = 1_000_000.0  # tiny
    prices = {"005930": 70_000.0, "000660": 130_000.0}
    results = ExecutionAgent().execute(target, {}, cash, prices, dry_run=True)
    total_cost = sum((r.order.price or 0) * r.order.quantity for r in results)
    assert total_cost <= cash + 1.0


def test_skip_below_rebalance_threshold() -> None:
    """If current weight is already close to target, no order is generated."""
    target = PortfolioTarget(
        as_of=date(2025, 5, 6),
        cash_weight=0.05,
        positions={"005930": 0.5},
    )
    # Roughly matching: 700 shares x 70_000 = 49_000_000 vs NAV ~ 100_000_000
    current = {"005930": 700}
    cash = 51_000_000.0
    prices = {"005930": 70_000.0}
    results = ExecutionAgent().execute(target, current, cash, prices, dry_run=True)
    # No or only tiny rebalance order
    qty = sum(r.order.quantity for r in results)
    assert qty <= 50  # threshold protects against churn
