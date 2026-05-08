"""TWAP slice spacing — child orders must wait between submissions."""
from __future__ import annotations

from datetime import date

from agents.execution_agent import ExecutionAgent
from common.types import PortfolioTarget


def _patch(monkeypatch, twap_enabled: bool, interval: int) -> None:
    from common import config as c

    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {
            "execution": {
                "twap_enabled": twap_enabled,
                "twap_slices": 4,
                "twap_interval_seconds": interval,
                "rebalance_buy_threshold": 0.04,
                "rebalance_sell_threshold": 0.08,
                "limit_offset_bps": 30,
                "dry_run_default": True,
            },
            "risk": {"max_position_weight": 0.10, "cash_buffer_min": 0.05},
        },
    )


def test_twap_disabled_does_not_wait(monkeypatch) -> None:
    _patch(monkeypatch, twap_enabled=False, interval=300)
    waits: list[int] = []
    monkeypatch.setattr(ExecutionAgent, "_twap_wait", staticmethod(waits.append))

    target = PortfolioTarget(
        as_of=date(2025, 5, 6), cash_weight=0.0, positions={"005930": 0.5}
    )
    ExecutionAgent().execute(
        target,
        current_positions={},
        cash=100_000_000,
        prices={"005930": 70_000.0},
        dry_run=True,
    )
    assert waits == []


def test_twap_slice_count_matches_setting(monkeypatch) -> None:
    """4 slices = 4 child orders for one parent BUY."""
    _patch(monkeypatch, twap_enabled=True, interval=0)
    agent = ExecutionAgent()
    from common.types import Order, Side

    parents = [Order(ticker="005930", side=Side.BUY, quantity=100, price=70_000.0)]
    children = agent._twap_slice(parents, 4)
    assert len(children) == 4
    assert sum(c.quantity for c in children) == 100  # no quantity lost


def test_twap_dry_run_skips_wait(monkeypatch) -> None:
    """Dry-run never sleeps — we want fast tests / fast paper."""
    _patch(monkeypatch, twap_enabled=True, interval=300)
    waits: list[int] = []
    monkeypatch.setattr(ExecutionAgent, "_twap_wait", staticmethod(waits.append))

    target = PortfolioTarget(
        as_of=date(2025, 5, 6), cash_weight=0.0, positions={"005930": 0.5}
    )
    ExecutionAgent().execute(
        target,
        current_positions={},
        cash=100_000_000,
        prices={"005930": 70_000.0},
        dry_run=True,
    )
    assert waits == []
