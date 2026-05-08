"""Verify the metrics surface works (with or without prometheus_client)."""
from __future__ import annotations

from common import metrics as m


def test_counters_increment_without_raising() -> None:
    """Whether prometheus_client is installed or stubbed, .inc() must not raise."""
    m.PHASE_RUNS.labels(phase="research", outcome="ok").inc()
    m.GUARD_FIRES.labels(guard="hard_stop").inc(3)
    m.KIS_REQUESTS.labels(endpoint="get_price", outcome="ok").inc()
    m.LLM_TOKENS.labels(agent="quant", kind="input").inc(1500)


def test_gauges_set_without_raising() -> None:
    m.NAV_KRW.set(123_456_789)
    m.N_POSITIONS.set(7)
    m.CASH_PCT.set(0.15)


def test_histogram_observe() -> None:
    m.KIS_LATENCY.labels(endpoint="place_order").observe(0.123)


def test_serve_is_idempotent_and_safe() -> None:
    """serve() must never raise — it's called from the scheduler entrypoint."""
    m.serve(port=0)  # port 0 = OS-assigned, harmless if available is False
    m.serve(port=0)


def test_is_available_returns_bool() -> None:
    assert isinstance(m.is_available(), bool)
