"""CLI: download historical Binance klines into data/history/.

Usage:
    python -m scripts.fetch_history \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT \
        --start 2023-01-01 \
        --end   2025-01-01 \
        --interval 1d

The output lives at `data/history/<interval>/<symbol>.ndjson` and is
ready to be consumed by `scripts/run_backtest.py --from-archive`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime

from src.data.binance_history import fetch_history_many, save_ndjson


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download historical Binance klines")
    p.add_argument(
        "--symbols",
        default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT",
        help="Comma-separated USDT spot symbols",
    )
    p.add_argument("--start", default="2023-01-01", help="YYYY-MM-DD")
    p.add_argument("--end", default="2025-01-01", help="YYYY-MM-DD")
    p.add_argument(
        "--interval",
        default="1d",
        choices=["1m", "5m", "15m", "1h", "4h", "1d", "1w"],
    )
    p.add_argument("--concurrency", type=int, default=4)
    return p.parse_args()


async def main_async() -> int:
    os.environ.setdefault("LOG_LEVEL", "INFO")
    args = parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)

    print(
        f"Fetching {len(symbols)} symbols @ {args.interval} "
        f"from {args.start} to {args.end} ..."
    )
    all_candles = await fetch_history_many(
        symbols=symbols,
        start=start,
        end=end,
        interval=args.interval,
        concurrency=args.concurrency,
    )
    for sym, candles in all_candles.items():
        if not candles:
            print(f"  {sym}: no candles returned")
            continue
        path = save_ndjson(sym, candles, interval=args.interval)
        print(f"  {sym}: {len(candles):5d} candles -> {path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
