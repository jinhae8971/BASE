"""Market data — pykrx / FinanceDataReader.

TODO(Phase 1): wire to pykrx for KOSPI200 prices, sector indexes, factor panel.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


def fetch_price_series(
    ticker: str, start: date, end: date
) -> pd.DataFrame:  # pragma: no cover - stub
    """OHLCV DataFrame indexed by date. Empty in Phase 0."""
    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


def fetch_sector_snapshot(as_of: date) -> dict[str, Any]:
    """Sector-level momentum / valuation / earnings revision snapshot."""
    return {
        "as_of": as_of.isoformat(),
        "sectors": [],  # list of {name, mom_3m, per, pbr, earn_rev, sentiment}
        "_stub": True,
    }


def fetch_factor_panel(as_of: date) -> dict[str, Any]:
    """Cross-sectional factor panel for the quant agent.

    Should return already-standardized z-scores per factor per ticker.
    """
    return {
        "as_of": as_of.isoformat(),
        "universe_size": 0,
        "factors": ["value", "momentum", "quality", "lowvol", "size"],
        "rows": [],  # list of {ticker, name, value, momentum, quality, lowvol, size}
        "_stub": True,
    }
