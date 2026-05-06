from __future__ import annotations

from datetime import date

from common.types import PortfolioTarget
from portfolio.position_state import get, set_pyramid_level, upsert_on_buy
from portfolio.pyramid import commit_pyramid_levels, evaluate_pyramid


def _patch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    from common import config as c

    c.get_env.cache_clear()
    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {
            "memory": {"journal_db": str(tmp_path / "j.sqlite")},
            "execution": {
                "pyramid_triggers": [0.20, 0.40],
                "pyramid_step_pct": 0.025,
            },
            "risk": {"max_position_weight": 0.10},
        },
    )


def _target() -> PortfolioTarget:
    return PortfolioTarget(
        as_of=date(2025, 5, 6),
        cash_weight=0.5,
        positions={"005930": 0.05},
    )


def test_pyramid_fires_at_plus_20pct(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    upsert_on_buy("005930", 100_000, 50, date(2025, 5, 1))
    target = _target()
    intents = evaluate_pyramid(
        target,
        current_positions={"005930": 50},
        prices={"005930": 122_000},  # +22% from entry
        nav=100_000_000,
    )
    assert len(intents) == 1
    assert intents[0].levels_added == 1
    # New target weight should rise above the prior 0.05
    assert target.positions["005930"] > 0.05


def test_pyramid_fires_both_levels_at_plus_45pct(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    upsert_on_buy("005930", 100_000, 50, date(2025, 5, 1))
    target = _target()
    intents = evaluate_pyramid(
        target,
        current_positions={"005930": 50},
        prices={"005930": 145_000},  # +45% from entry
        nav=100_000_000,
    )
    assert intents[0].levels_added == 2  # both +20% and +40% levels


def test_pyramid_does_not_refire_after_commit(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    upsert_on_buy("005930", 100_000, 50, date(2025, 5, 1))
    target = _target()
    intents = evaluate_pyramid(
        target,
        current_positions={"005930": 50},
        prices={"005930": 125_000},
        nav=100_000_000,
    )
    assert intents
    commit_pyramid_levels(intents)
    state = get("005930")
    assert state is not None
    assert state.pyramid_levels == 1

    # Second evaluation at the same price level — should NOT refire.
    target2 = _target()
    again = evaluate_pyramid(
        target2,
        current_positions={"005930": 50},
        prices={"005930": 125_000},
        nav=100_000_000,
    )
    assert again == []


def test_pyramid_skips_names_not_in_target(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    upsert_on_buy("005930", 100_000, 50, date(2025, 5, 1))
    target = PortfolioTarget(as_of=date(2025, 5, 6), cash_weight=1.0, positions={})
    intents = evaluate_pyramid(
        target,
        current_positions={"005930": 50},
        prices={"005930": 125_000},
        nav=100_000_000,
    )
    assert intents == []


def test_pyramid_capped_at_max_position_weight(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    upsert_on_buy("005930", 100_000, 50, date(2025, 5, 1))
    set_pyramid_level("005930", 0)
    # Already near the cap (9.5%); pyramid should bump only to 10%
    target = PortfolioTarget(
        as_of=date(2025, 5, 6),
        cash_weight=0.0,
        positions={"005930": 0.095},
    )
    intents = evaluate_pyramid(
        target,
        current_positions={"005930": 78},  # 78 * 122k / 100M = ~9.5%
        prices={"005930": 122_000},
        nav=100_000_000,
    )
    if intents:
        # Capped at 10%
        assert target.positions["005930"] <= 0.10 + 1e-9
