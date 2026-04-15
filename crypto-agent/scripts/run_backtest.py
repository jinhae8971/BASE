"""CLI entrypoint for the backtest engine.

Two modes:

1. Synthetic drift (default) — useful for wiring tests:

    python -m scripts.run_backtest --days 365 --btc-drift 0.003

2. Real Binance history from a previously-downloaded archive:

    python -m scripts.fetch_history --symbols BTCUSDT,ETHUSDT --start 2023-01-01 --end 2025-01-01
    python -m scripts.run_backtest --from-archive --symbols BTCUSDT,ETHUSDT

The archive path is `data/history/<interval>/<symbol>.ndjson` which is
what `scripts/fetch_history.py` writes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta

from src.backtest.engine import BacktestEngine
from src.backtest.heuristic_llm import HeuristicLLMClient
from src.backtest.portfolio_sim import SimConfig
from src.backtest.provider import HistoricalSnapshotProvider
from src.data.binance_history import load_archive
from src.data.binance_md import Candle


def _synthetic_candles(
    n_days: int,
    start_price: float,
    daily_drift: float,
) -> list[Candle]:
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    price = start_price
    out: list[Candle] = []
    for i in range(n_days):
        price *= 1 + daily_drift
        out.append(
            Candle(
                open_time=t0 + timedelta(days=i),
                open=price * 0.998,
                high=price * 1.005,
                low=price * 0.995,
                close=price,
                volume=1_000,
                quote_volume=price * 1_000,
            )
        )
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a backtest")
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--initial-capital", type=float, default=1000.0)
    p.add_argument("--fee-bps", type=float, default=10.0)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--btc-drift", type=float, default=0.003)
    p.add_argument("--alt-drift", type=float, default=0.002)
    p.add_argument("--rebalance-every", type=int, default=1)
    p.add_argument(
        "--from-archive",
        action="store_true",
        help="Use real Binance history from data/history/<interval>/<symbol>.ndjson",
    )
    p.add_argument("--interval", default="1d", help="Kline interval for --from-archive")
    return p.parse_args()


async def main_async() -> int:
    # Silence structlog for readable CLI output.
    os.environ.setdefault("LOG_LEVEL", "ERROR")

    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if args.from_archive:
        candles = load_archive(symbols, interval=args.interval)
        empty = [s for s, cs in candles.items() if not cs]
        if empty:
            print(
                f"ERROR: no archive data for {empty}. "
                f"Run: python -m scripts.fetch_history --symbols {','.join(empty)}"
            )
            return 2
    else:
        candles = {
            sym: _synthetic_candles(
                args.days,
                start_price=30_000.0 if sym == "BTCUSDT" else 2_000.0,
                daily_drift=args.btc_drift if sym == "BTCUSDT" else args.alt_drift,
            )
            for sym in symbols
        }
    provider = HistoricalSnapshotProvider(
        universe=symbols,
        candles_by_symbol=candles,
        warmup_candles=30,
    )

    engine = BacktestEngine(
        provider=provider,
        llm_client=HeuristicLLMClient(),
        sim_config=SimConfig(
            initial_cash=args.initial_capital,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
        ),
        rebalance_every_n_days=args.rebalance_every,
    )
    result = await engine.run()

    print(result.report.summary())
    print(
        json.dumps(
            {
                "num_orders": result.num_orders,
                "num_fills": len(result.fills),
                "days": len(result.days),
                "final_equity": round(result.equity[-1], 2),
                "btc_equity": round(result.btc_equity[-1], 2),
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
