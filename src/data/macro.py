"""Macro indicators — ECOS / FRED / yfinance.

TODO(Phase 1): wire to ECOS (한은) + FRED + yfinance.
Returns a compact dict suitable for LLM context injection.
"""
from __future__ import annotations

from datetime import date
from typing import Any


def fetch_macro_snapshot(as_of: date) -> dict[str, Any]:
    """Return a snapshot of macro indicators for `as_of`.

    Schema (Phase 1):
    {
        "rates": {"kr_3y": 3.21, "us_10y": 4.28, "real_rate": 1.75},
        "fx": {"usd_krw": 1372.5, "dxy": 104.2},
        "commodities": {"oil_wti": 78.2, "copper": 4.12, "gold": 2350},
        "equity": {"kospi": 2810, "kospi_mom_3m": 0.04, "vix": 14.2},
        "credit": {"hy_oas": 3.55},
        "leading": {"oecd_cli": 99.8, "kr_lei_mom": -0.002},
        "events_next_7d": ["FOMC", "KRX op-ex"]
    }
    """
    # Phase-0 placeholder — zero data, lets the pipeline run end-to-end.
    return {
        "as_of": as_of.isoformat(),
        "rates": {},
        "fx": {},
        "commodities": {},
        "equity": {},
        "credit": {},
        "leading": {},
        "events_next_7d": [],
        "_stub": True,
    }
