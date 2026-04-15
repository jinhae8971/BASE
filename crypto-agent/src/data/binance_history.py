"""Bulk historical Binance kline fetcher.

Downloads OHLCV candles from Binance's public REST `/api/v3/klines`
endpoint in 1000-candle pages, handles start/end windowing correctly,
writes each symbol's series to `data/history/<interval>/<symbol>.ndjson`
and loads it back for the backtest engine.

Why NDJSON instead of Parquet:
  - pandas-free stack keeps the import time fast and the dependency
    surface small,
  - NDJSON is grep-able and diff-able in a text editor,
  - backtests are the only consumer and they're already pandas-free,
  - conversion to Parquet is a trivial one-liner if Phase 8 ever needs
    bulk columnar loads.

The on-disk format is one JSON object per line containing the raw
Binance kline array plus an `ot` ISO-8601 open time so it's easy to
eyeball.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from src.data.binance_md import Candle
from src.data.http import build_client, get_json
from src.logging import get_logger

log = get_logger("binance_history")

BINANCE_BASE = "https://api.binance.com"
HISTORY_ROOT = Path("data/history")
KLINES_LIMIT = 1000  # Binance cap per request


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


async def fetch_history(
    symbol: str,
    start: datetime,
    end: datetime,
    interval: str = "1d",
    client: httpx.AsyncClient | None = None,
) -> list[Candle]:
    """Fetch every candle in [start, end] for `symbol`, paginating as needed.

    Binance returns at most 1000 candles per request; for 1d interval
    that's ~2.7 years per call. For shorter intervals (1h, 15m), this
    function loops and advances `startTime` after each page.
    """
    own_client = client is None
    if own_client:
        client = build_client(base_url=BINANCE_BASE)
    try:
        assert client is not None
        out: list[Candle] = []
        cursor = start
        step_ms = _interval_ms(interval)
        while cursor <= end:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": int(cursor.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
                "limit": KLINES_LIMIT,
            }
            raw = await get_json(client, "/api/v3/klines", params=params)
            if not raw:
                break
            for row in raw:
                out.append(_parse_kline(row))
            # Advance cursor one step past the last returned open-time.
            last_open_ms = raw[-1][0]
            next_cursor = datetime.fromtimestamp(
                (last_open_ms + step_ms) / 1000, tz=UTC
            )
            if next_cursor <= cursor:
                break  # prevent infinite loop on malformed response
            cursor = next_cursor
            if len(raw) < KLINES_LIMIT:
                break  # last page
        log.info("history.fetched", symbol=symbol, interval=interval, count=len(out))
        return out
    finally:
        if own_client and client is not None:
            await client.aclose()


async def fetch_history_many(
    symbols: list[str],
    start: datetime,
    end: datetime,
    interval: str = "1d",
    concurrency: int = 4,
) -> dict[str, list[Candle]]:
    """Fetch many symbols concurrently with a bounded semaphore."""
    sem = asyncio.Semaphore(concurrency)
    client = build_client(base_url=BINANCE_BASE)
    try:
        async def _one(sym: str) -> tuple[str, list[Candle]]:
            async with sem:
                candles = await fetch_history(sym, start, end, interval, client=client)
                return sym, candles

        pairs = await asyncio.gather(*(_one(s) for s in symbols))
        return dict(pairs)
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Persistence (NDJSON)
# ---------------------------------------------------------------------------


def _path_for(symbol: str, interval: str, root: Path | None = None) -> Path:
    base = root or HISTORY_ROOT
    return base / interval / f"{symbol}.ndjson"


def save_ndjson(
    symbol: str,
    candles: list[Candle],
    interval: str = "1d",
    root: Path | None = None,
) -> Path:
    path = _path_for(symbol, interval, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for c in candles:
            row = {
                "ot": c.open_time.isoformat(),
                "o": c.open,
                "h": c.high,
                "l": c.low,
                "c": c.close,
                "v": c.volume,
                "qv": c.quote_volume,
            }
            f.write(json.dumps(row) + "\n")
    return path


def load_ndjson(
    symbol: str,
    interval: str = "1d",
    root: Path | None = None,
) -> list[Candle]:
    path = _path_for(symbol, interval, root)
    if not path.exists():
        return []
    out: list[Candle] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            out.append(
                Candle(
                    open_time=datetime.fromisoformat(row["ot"]),
                    open=float(row["o"]),
                    high=float(row["h"]),
                    low=float(row["l"]),
                    close=float(row["c"]),
                    volume=float(row["v"]),
                    quote_volume=float(row["qv"]),
                )
            )
    return out


def load_archive(
    symbols: list[str],
    interval: str = "1d",
    root: Path | None = None,
) -> dict[str, list[Candle]]:
    return {s: load_ndjson(s, interval=interval, root=root) for s in symbols}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _interval_ms(interval: str) -> int:
    """Convert a Binance interval string into milliseconds."""
    unit = interval[-1]
    qty = int(interval[:-1])
    if unit == "m":
        return qty * 60_000
    if unit == "h":
        return qty * 3_600_000
    if unit == "d":
        return qty * 86_400_000
    if unit == "w":
        return qty * 7 * 86_400_000
    raise ValueError(f"unsupported interval {interval!r}")


def _parse_kline(row: list[Any]) -> Candle:
    return Candle(
        open_time=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
        quote_volume=float(row[7]),
    )
