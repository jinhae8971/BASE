"""FRED macro series client.

Series used: DGS10, DGS2, DTWEXBGS (DXY proxy), CPIAUCSL, FEDFUNDS, VIXCLS.
Free tier requires a key (set FRED_API_KEY). Daily refresh is enough.
"""

from __future__ import annotations

SERIES = {
    "10Y": "DGS10",
    "2Y": "DGS2",
    "DXY": "DTWEXBGS",
    "CPI": "CPIAUCSL",
    "FFR": "FEDFUNDS",
    "VIX": "VIXCLS",
}


async def fetch_latest() -> dict[str, float]:
    """Return latest observation per series. Stub returns zeros."""
    return {k: 0.0 for k in SERIES}
