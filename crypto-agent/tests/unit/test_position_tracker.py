"""PositionTracker lifecycle tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.learning.position_tracker import EntryContext, PositionTracker


def _ctx(run_id: str = "r1") -> EntryContext:
    return EntryContext(
        run_id=run_id,
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
        agent_payloads={"quant": {"signal": 0.5}},
        macro_regime="risk-on",
        universe=["BTCUSDT"],
    )


def test_buy_opens_new_position() -> None:
    t = PositionTracker()
    t.on_buy("BTCUSDT", qty=0.5, price=100.0, at=datetime(2025, 1, 1, tzinfo=UTC), entry=_ctx())
    assert t.is_open("BTCUSDT")
    pos = t.open_positions["BTCUSDT"]
    assert pos.qty == 0.5
    assert pos.avg_entry_price == 100.0


def test_pyramid_buy_weighted_average_keeps_original_context() -> None:
    t = PositionTracker()
    t.on_buy("BTCUSDT", 1.0, 100.0, datetime(2025, 1, 1, tzinfo=UTC), _ctx("run-a"))
    t.on_buy("BTCUSDT", 1.0, 200.0, datetime(2025, 1, 2, tzinfo=UTC), _ctx("run-b"))
    pos = t.open_positions["BTCUSDT"]
    assert pos.qty == pytest.approx(2.0)
    assert pos.avg_entry_price == pytest.approx(150.0)
    # First-entry-wins: thesis under audit is run-a, not run-b.
    assert pos.entry.run_id == "run-a"


def test_partial_sell_does_not_emit_close() -> None:
    t = PositionTracker()
    t.on_buy("BTCUSDT", 2.0, 100.0, datetime(2025, 1, 1, tzinfo=UTC), _ctx())
    closed = t.on_sell("BTCUSDT", 1.0, 120.0, datetime(2025, 1, 5, tzinfo=UTC))
    assert closed is None
    pos = t.open_positions["BTCUSDT"]
    assert pos.qty == pytest.approx(1.0)
    assert pos.realized_pnl_usd == pytest.approx(20.0)  # (120-100) * 1


def test_full_sell_emits_closed_position_with_pnl_and_holding_period() -> None:
    t = PositionTracker()
    opened = datetime(2025, 1, 1, tzinfo=UTC)
    t.on_buy("BTCUSDT", 2.0, 100.0, opened, _ctx("r1"))
    closed_at = opened + timedelta(days=30)
    closed = t.on_sell("BTCUSDT", 2.0, 130.0, closed_at)

    assert closed is not None
    assert closed.symbol == "BTCUSDT"
    assert closed.avg_entry_price == pytest.approx(100.0)
    assert closed.exit_price == pytest.approx(130.0)
    assert closed.realized_pnl_pct == pytest.approx(30.0)
    assert closed.holding_period_days == pytest.approx(30.0)
    assert closed.entry.run_id == "r1"
    # Position removed from open set.
    assert not t.is_open("BTCUSDT")


def test_sell_with_no_position_returns_none() -> None:
    t = PositionTracker()
    assert t.on_sell("BTCUSDT", 1.0, 100.0, datetime(2025, 1, 1, tzinfo=UTC)) is None


def test_sell_clamps_to_held_qty_and_still_closes() -> None:
    t = PositionTracker()
    t.on_buy("BTCUSDT", 1.0, 100.0, datetime(2025, 1, 1, tzinfo=UTC), _ctx())
    # Ask to sell 5 BTC when we only hold 1 — should close on 1.
    closed = t.on_sell("BTCUSDT", 5.0, 150.0, datetime(2025, 1, 2, tzinfo=UTC))
    assert closed is not None
    assert closed.total_qty == pytest.approx(1.0)
    assert closed.realized_pnl_usd == pytest.approx(50.0)
