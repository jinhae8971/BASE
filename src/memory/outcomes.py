"""Backfill realized outcomes (1w/1m/3m forward returns) onto the Decision Journal.

Run this from a daily cron — it's idempotent.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from common.logging import get_logger
from data.market import fetch_price_series

from .journal import DecisionJournal

log = get_logger(__name__)


def _forward_return(ticker: str, base_dt: datetime, days: int) -> float | None:
    start = base_dt.date() - timedelta(days=3)
    end = base_dt.date() + timedelta(days=days + 14)
    df = fetch_price_series(ticker, start, end)
    if df.empty:
        return None
    closes = df["Close"].astype(float)
    base = closes[closes.index >= pd.Timestamp(base_dt)]
    if base.empty:
        return None
    px0 = float(base.iloc[0])
    target_dt = pd.Timestamp(base_dt) + pd.Timedelta(days=days)
    after = closes[closes.index >= target_dt]
    if after.empty:
        return None
    px1 = float(after.iloc[0])
    if px0 <= 0:
        return None
    return px1 / px0 - 1.0


def update_outcomes() -> dict[str, int]:
    """Fill missing 1w/1m/3m outcomes for ticker-level decisions."""
    j = DecisionJournal()
    counts = {"1w": 0, "1m": 0, "3m": 0}
    for horizon, days in (("1w", 7), ("1m", 30), ("3m", 90)):
        for row in j.pending_outcomes(horizon):
            ticker = row.get("ticker")
            if not ticker:
                continue
            try:
                base_dt = datetime.fromisoformat(row["ts"])
            except ValueError:
                continue
            ret = _forward_return(ticker, base_dt, days)
            if ret is None:
                continue
            kw = {f"outcome_{horizon}": float(ret)}
            j.update_outcome(row["id"], **kw)
            counts[horizon] += 1
    log.info("outcomes.updated", **counts)
    return counts


def main() -> None:
    update_outcomes()


if __name__ == "__main__":
    main()
