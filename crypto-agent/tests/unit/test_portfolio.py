"""Optimizer and risk guardrail unit tests."""

from __future__ import annotations

from src.portfolio.aggregator import AggregatedSignal
from src.portfolio.optimizer import optimize
from src.portfolio.risk import (
    GuardAction,
    PortfolioState,
    check_mdd,
    check_order_sanity,
)


def test_optimizer_all_zero_signals_goes_to_cash() -> None:
    sigs = {
        s: AggregatedSignal(symbol=s, score=0.0, contributions={})
        for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    }
    alloc = optimize(sigs)
    assert alloc.cash_pct == 100.0
    assert all(w == 0.0 for w in alloc.weights.values())


def test_optimizer_enforces_core_minimum() -> None:
    sigs = {
        "BTCUSDT": AggregatedSignal("BTCUSDT", 0.1, {}),
        "ETHUSDT": AggregatedSignal("ETHUSDT", 0.1, {}),
        "SOLUSDT": AggregatedSignal("SOLUSDT", 0.8, {}),
    }
    alloc = optimize(sigs, macro_cash_floor_pct=0.0)
    core = alloc.weights["BTCUSDT"] + alloc.weights["ETHUSDT"]
    assert core >= 40.0 - 1e-6


def test_mdd_circuit_breaker_trips_at_15_pct() -> None:
    state = PortfolioState(
        equity_usd=850.0, peak_equity_usd=1000.0, daily_pnl_pct=0.0, weekly_pnl_pct=0.0
    )
    decision = check_mdd(state)
    assert decision.action == GuardAction.HALT_ALL


def test_order_sanity_downsizes_oversized_order() -> None:
    decision = check_order_sanity(order_qty_usd=500.0, equity_usd=1000.0)
    assert decision.action == GuardAction.DOWNSIZE
    assert decision.new_qty_usd == 300.0
