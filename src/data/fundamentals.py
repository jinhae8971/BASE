"""Financial statements from DART (OpenDartReader).

TODO(Phase 1): build a nightly snapshot of fundamentals and surface the most
recent quarterly/annual metrics for the ValueAgent.
"""
from __future__ import annotations

from datetime import date
from typing import Any


def fetch_value_candidates(as_of: date) -> dict[str, Any]:
    """Return a pre-filtered set of value candidates for the ValueAgent.

    Typical schema (Phase 1):
    {
        "as_of": "2025-01-15",
        "candidates": [
            {"ticker": "005930", "name": "삼성전자", "sector": "반도체",
             "per_fwd": 9.1, "pbr": 1.2, "roe": 14.3, "de_ratio": 28.5,
             "fcf_yield": 0.06, "dividend_yield": 0.022,
             "intrinsic_value": 95000, "current_price": 72000}
        ]
    }
    """
    return {
        "as_of": as_of.isoformat(),
        "candidates": [],
        "_stub": True,
    }
