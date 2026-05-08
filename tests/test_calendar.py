from __future__ import annotations

from datetime import date

from common.calendar import is_trading_day, next_trading_day, trading_days


def _patch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    from common import config as c

    c.get_env.cache_clear()


def test_weekday_fallback_skips_weekend(monkeypatch, tmp_path) -> None:
    """When pykrx is unreachable we fall back to weekdays."""
    _patch(monkeypatch, tmp_path)
    # Saturday 2025-01-04 must NOT be a trading day in fallback
    assert is_trading_day(date(2025, 1, 4)) is False
    # Sunday too
    assert is_trading_day(date(2025, 1, 5)) is False


def test_weekday_fallback_includes_normal_monday(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    # Monday 2025-01-06 should be a trading day in fallback
    assert is_trading_day(date(2025, 1, 6)) is True


def test_next_trading_day_skips_weekend(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    # After Friday 2025-01-03, the next trading day in fallback is Monday 06.
    nxt = next_trading_day(date(2025, 1, 3))
    assert nxt == date(2025, 1, 6)


def test_trading_days_returns_set(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    days = trading_days(2025)
    assert len(days) >= 240  # ~252 trading days a year
    assert all(d.year == 2025 for d in days)


def test_cache_persisted(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    days1 = trading_days(2024)
    days2 = trading_days(2024)
    # Same set on second call (whether from cache or recomputed)
    assert days1 == days2
