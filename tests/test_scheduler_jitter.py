"""Verify that APScheduler honors the integer-seconds jitter parameter.

If jitter is silently dropped the order_phase fires at the same second
every day → easy HFT pattern target.
"""
from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger


def test_cron_trigger_accepts_integer_jitter() -> None:
    """jitter=N seconds must construct without raising."""
    trig = CronTrigger(hour=9, minute=5, day_of_week="mon-fri", jitter=60)
    assert trig is not None


def test_jitter_actually_perturbs_fire_time() -> None:
    """Two consecutive next_fire_time calls with jitter should differ from
    the un-jittered baseline at least once across a small sample."""
    import datetime as dt

    base = dt.datetime(2025, 1, 6, 8, 0, 0)  # a Monday morning, well before 09:05

    no_jitter = CronTrigger(hour=9, minute=5, day_of_week="mon-fri", jitter=None)
    with_jitter = CronTrigger(hour=9, minute=5, day_of_week="mon-fri", jitter=60)

    plain = no_jitter.get_next_fire_time(None, base)
    samples = {
        with_jitter.get_next_fire_time(None, base + dt.timedelta(days=i))
        for i in range(7, 14)  # different Mondays so RNG seed differs
    }
    # At least one sample should differ in seconds — the jitter window is 60s.
    assert any(
        abs((s - plain.replace(year=s.year, month=s.month, day=s.day)).total_seconds())
        > 0
        or s.second != 0
        for s in samples
    )
