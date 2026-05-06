from __future__ import annotations

from datetime import date

from common.types import PortfolioTarget
from scheduler import state as state_store


def test_save_and_load_target(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    from common import config as c

    c.get_env.cache_clear()

    t = PortfolioTarget(
        as_of=date(2025, 5, 6),
        cash_weight=0.1,
        positions={"005930": 0.4, "000660": 0.3},
        rationale="unit test",
    )
    state_store.save_target(t, extra={"foo": "bar"})
    loaded = state_store.load_target(date(2025, 5, 6))
    assert loaded is not None
    target, extra = loaded
    assert target.cash_weight == 0.1
    assert target.positions["005930"] == 0.4
    assert extra["foo"] == "bar"


def test_load_missing_returns_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    from common import config as c

    c.get_env.cache_clear()
    assert state_store.load_target(date(1999, 1, 1)) is None
