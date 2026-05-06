from __future__ import annotations

from datetime import date

from common.types import Side
from portfolio.position_state import (
    PositionState,
    get,
    remove,
    update_peak,
    upsert_on_buy,
)
from portfolio.stops import evaluate_stops, stop_orders


def _fresh(monkeypatch, tmp_path) -> None:
    """Point the SQLite path at a temp DB."""
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    from common import config as c

    c.get_env.cache_clear()
    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        c,
        "load_yaml_settings",
        lambda: {
            "memory": {"journal_db": str(tmp_path / "j.sqlite")},
            "risk": {
                "hard_stop_pct": 0.12,
                "trailing_take_pct": 0.10,
                "trailing_min_profit": 0.05,
            },
        },
    )


def test_upsert_on_buy_first_then_average(monkeypatch, tmp_path) -> None:
    _fresh(monkeypatch, tmp_path)
    upsert_on_buy("005930", 70_000, 100, date(2025, 5, 1))
    s = get("005930")
    assert s is not None
    assert s.entry_price == 70_000
    assert s.qty == 100

    upsert_on_buy("005930", 80_000, 100, date(2025, 5, 5))
    s2 = get("005930")
    assert s2 is not None
    # weighted avg entry = 75_000, peak follows the higher fill
    assert s2.entry_price == 75_000
    assert s2.peak_price == 80_000
    assert s2.qty == 200


def test_hard_stop_fires_below_threshold(monkeypatch, tmp_path) -> None:
    _fresh(monkeypatch, tmp_path)
    upsert_on_buy("005930", 100_000, 50, date(2025, 5, 1))
    # Down 13% — should fire hard stop (12%)
    signals = evaluate_stops({"005930": 50}, {"005930": 87_000})
    assert len(signals) == 1
    assert "hard_stop" in signals[0].reason


def test_trailing_take_only_after_min_profit(monkeypatch, tmp_path) -> None:
    _fresh(monkeypatch, tmp_path)
    upsert_on_buy("005930", 100_000, 50, date(2025, 5, 1))
    # Push peak to 120k (+20%)
    update_peak("005930", 120_000, date(2025, 5, 8))
    # Now drop 11% from peak (to ~106800). Pnl from entry still +6.8% > 5% min.
    signals = evaluate_stops({"005930": 50}, {"005930": 106_800})
    assert len(signals) == 1
    assert "trail" in signals[0].reason


def test_trailing_does_not_fire_when_only_small_profit(monkeypatch, tmp_path) -> None:
    _fresh(monkeypatch, tmp_path)
    upsert_on_buy("005930", 100_000, 50, date(2025, 5, 1))
    update_peak("005930", 103_000, date(2025, 5, 4))  # only +3% profit, below 5% min
    # 12% drop from peak — but we should NOT trail since pnl < min_profit
    signals = evaluate_stops({"005930": 50}, {"005930": 90_640})
    # However hard_stop = -9.36% from entry — also doesn't fire (< 12%)
    assert all("trail" not in s.reason for s in signals)


def test_stop_orders_emit_market_sell(monkeypatch, tmp_path) -> None:
    _fresh(monkeypatch, tmp_path)
    upsert_on_buy("005930", 100_000, 50, date(2025, 5, 1))
    signals = evaluate_stops({"005930": 50}, {"005930": 80_000})
    orders = stop_orders(signals, {"005930": 80_000})
    assert orders[0].side is Side.SELL
    assert orders[0].order_type == "market"
    assert orders[0].quantity == 50


def test_remove_clears(monkeypatch, tmp_path) -> None:
    _fresh(monkeypatch, tmp_path)
    upsert_on_buy("005930", 100_000, 50, date(2025, 5, 1))
    assert get("005930") is not None
    remove("005930")
    assert get("005930") is None


def test_position_state_pnl_helpers() -> None:
    s = PositionState(
        ticker="x",
        entry_price=100_000,
        entry_date=date(2025, 1, 1),
        peak_price=120_000,
        peak_date=date(2025, 5, 1),
        qty=10,
    )
    assert abs(s.total_pnl_pct(110_000) - 0.10) < 1e-9
    assert abs(s.trailing_drop_pct(108_000) + 0.10) < 1e-6
