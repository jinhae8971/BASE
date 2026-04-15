"""FRED macro series client.

Uses the public `fredgraph.csv` endpoint which requires no key and returns
CSV (two columns: DATE, VALUE). This avoids the fredapi dependency and the
api_key requirement. Daily refresh is enough for our macro agent.

Series used (keys map to FRED series IDs):
  10Y  DGS10      : 10Y Treasury constant maturity
  2Y   DGS2       : 2Y Treasury constant maturity
  DXY  DTWEXBGS   : Trade-weighted USD broad index (DXY proxy)
  CPI  CPIAUCSL   : CPI All Urban Consumers
  FFR  FEDFUNDS   : Effective federal funds rate
  VIX  VIXCLS     : CBOE Volatility Index
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from src.data.archive import append as archive_append
from src.data.cache import cached, default_cache
from src.data.http import build_client, configure_rate_limit
from src.logging import get_logger

log = get_logger("fred")

BASE_URL = "https://fred.stlouisfed.org"
HOST = "fred.stlouisfed.org"

configure_rate_limit(HOST, requests=30, per_seconds=60.0)

SERIES: dict[str, str] = {
    "10Y": "DGS10",
    "2Y": "DGS2",
    "DXY": "DTWEXBGS",
    "CPI": "CPIAUCSL",
    "FFR": "FEDFUNDS",
    "VIX": "VIXCLS",
}


@dataclass
class MacroSnapshot:
    values: dict[str, float]     # key -> latest numeric observation
    as_of: dict[str, str]         # key -> ISO date string


class FredClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or build_client(base_url=BASE_URL)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> FredClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def latest(self) -> MacroSnapshot:
        async def _load() -> dict[str, Any]:
            values: dict[str, float] = {}
            as_of: dict[str, str] = {}
            for key, series in SERIES.items():
                try:
                    resp = await self._client.get(
                        "/graph/fredgraph.csv", params={"id": series}
                    )
                    resp.raise_for_status()
                    date, val = _parse_last_row(resp.text)
                    if val is not None:
                        values[key] = val
                        as_of[key] = date
                except Exception as exc:  # noqa: BLE001
                    log.warning("fred.fetch_failed", series=series, err=str(exc))
            archive_append("fred_latest", {"values": values, "as_of": as_of})
            return {"values": values, "as_of": as_of}

        raw = await cached("fred:latest", ttl=21_600, loader=_load, cache=default_cache())
        return MacroSnapshot(values=raw["values"], as_of=raw["as_of"])


def _parse_last_row(csv_text: str) -> tuple[str, float | None]:
    """Return (date, value) for the last non-'.' row in a FRED CSV."""
    last_date = ""
    last_val: float | None = None
    for line in csv_text.strip().splitlines()[1:]:  # skip header
        parts = line.split(",")
        if len(parts) < 2:
            continue
        date, val = parts[0].strip(), parts[1].strip()
        if val == "." or not val:
            continue
        try:
            last_val = float(val)
            last_date = date
        except ValueError:
            continue
    return last_date, last_val
