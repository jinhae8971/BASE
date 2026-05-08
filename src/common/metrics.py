"""Prometheus-style metrics for the running system.

We expose a tiny in-process metrics surface (counters, gauges, histograms)
that ``scheduler/metrics_server.py`` serves over HTTP at ``/metrics`` for
Prometheus scraping. Falls back to no-op when ``prometheus_client`` is
unavailable so the rest of the system runs unaffected.

Naming follows the ``mais_<area>_<metric>`` convention.
"""
from __future__ import annotations

from typing import Any

try:
    from prometheus_client import (  # type: ignore
        Counter,
        Gauge,
        Histogram,
        start_http_server,
    )

    _AVAILABLE = True
except Exception:
    _AVAILABLE = False

    class _NoOp:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            ...

        def labels(self, *_a: Any, **_kw: Any) -> _NoOp:
            return self

        def inc(self, *_a: Any, **_kw: Any) -> None:
            ...

        def set(self, *_a: Any, **_kw: Any) -> None:
            ...

        def observe(self, *_a: Any, **_kw: Any) -> None:
            ...

    Counter = Gauge = Histogram = _NoOp  # type: ignore[assignment,misc]

    def start_http_server(*_a: Any, **_kw: Any) -> None:  # type: ignore
        return None


# --- Surface area -----------------------------------------------------

# KIS broker traffic
KIS_REQUESTS = Counter(
    "mais_kis_requests_total",
    "Total KIS Open API requests",
    ["endpoint", "outcome"],
)
KIS_LATENCY = Histogram(
    "mais_kis_latency_seconds",
    "KIS Open API response latency",
    ["endpoint"],
)

# Pipeline phases
PHASE_RUNS = Counter(
    "mais_phase_runs_total",
    "Pipeline phase invocations",
    ["phase", "outcome"],
)

# Risk guard fires
GUARD_FIRES = Counter(
    "mais_risk_guard_fires_total",
    "Risk guard activations",
    ["guard"],  # daily_loss_kill | mdd | turnover | overnight_shock | stop | trail
)

# Book / NAV
NAV_KRW = Gauge("mais_nav_krw", "Current portfolio NAV (KRW)")
N_POSITIONS = Gauge("mais_n_positions", "Number of open positions")
CASH_PCT = Gauge("mais_cash_weight", "Portfolio cash weight (0..1)")

# LLM cost tracking
LLM_TOKENS = Counter(
    "mais_llm_tokens_total",
    "Total tokens consumed",
    ["agent", "kind"],  # kind: input | output
)


def serve(port: int = 9100) -> None:
    """Start the Prometheus exporter on the given port. Idempotent.

    Called from the scheduler entrypoint. Safe to call when the
    ``prometheus_client`` package is missing — becomes a no-op.
    """
    if not _AVAILABLE:
        return
    import contextlib

    with contextlib.suppress(OSError):
        # Already bound — hot-reload case. Ignore.
        start_http_server(port)


def is_available() -> bool:
    return _AVAILABLE
