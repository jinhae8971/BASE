"""CLI entrypoint for the backtest engine.

Three modes:

1. Synthetic drift (default) — for wiring tests:

    python -m scripts.run_backtest --days 365 --btc-drift 0.003

2. Real Binance history via heuristic LLM client (default with
   `--from-archive`):

    python -m scripts.fetch_history --symbols BTCUSDT,ETHUSDT --start 2023-01-01 --end 2025-01-01
    python -m scripts.run_backtest --from-archive --symbols BTCUSDT,ETHUSDT

3. Real Binance history via the real Anthropic API (paid):

    python -m scripts.run_backtest --from-archive --symbols BTCUSDT,ETHUSDT --real-llm --max-days 60

`--real-llm` wires in `AnthropicLLMClient` instead of the deterministic
heuristic baseline. The CLI prints a cost estimate up front, caps the
backtest at `--max-days` most recent days of the archive, and refuses to
start if `ANTHROPIC_API_KEY` is unset.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta

from src.backtest.engine import BacktestEngine
from src.backtest.heuristic_llm import HeuristicLLMClient
from src.backtest.portfolio_sim import SimConfig
from src.backtest.provider import HistoricalSnapshotProvider
from src.data.binance_history import load_archive
from src.data.binance_md import Candle


# Rough cost estimate per single DailyWorkflow run using the real LLM.
# See README / src/llm.py PRICING — updated per Anthropic pricing changes.
COST_PER_DAY_REAL_LLM_USD = 0.15
DEFAULT_MAX_DAYS_REAL_LLM = 60


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


def _trim_to_last_n_days(
    candles: dict[str, list[Candle]],
    n: int,
    warmup: int,
) -> dict[str, list[Candle]]:
    """Keep only the most recent `n + warmup` candles per symbol."""
    keep = n + warmup
    return {sym: cs[-keep:] if len(cs) > keep else cs for sym, cs in candles.items()}


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
    p.add_argument(
        "--real-llm",
        action="store_true",
        help="Use AnthropicLLMClient instead of the HeuristicLLMClient "
             "baseline. Requires ANTHROPIC_API_KEY and burns real credits.",
    )
    p.add_argument(
        "--max-days",
        type=int,
        default=0,
        help="Limit the backtest to the most recent N days of the archive. "
             "Defaults to 60 in --real-llm mode for cost safety; 0 = no limit.",
    )
    p.add_argument(
        "--estimate-only",
        action="store_true",
        help="Print the cost estimate for --real-llm and exit without running.",
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Skip the interactive y/N prompt before a --real-llm run.",
    )
    return p.parse_args()


def _build_llm_client(args: argparse.Namespace):
    if not args.real_llm:
        return HeuristicLLMClient()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: --real-llm requires ANTHROPIC_API_KEY in the environment.",
            file=sys.stderr,
        )
        sys.exit(2)

    from src.llm import AnthropicLLMClient

    return AnthropicLLMClient()


def _print_cost_estimate(days: int) -> float:
    est = days * COST_PER_DAY_REAL_LLM_USD
    print("------------------------------------------------------------")
    print(f"REAL LLM BACKTEST — cost estimate")
    print(f"  days          : {days}")
    print(f"  per-day est   : ${COST_PER_DAY_REAL_LLM_USD:.3f}")
    print(f"  total estimate: ${est:.2f}")
    print("  note: this is a rough pre-flight number. Actual cost depends")
    print("        on prompt caching hits, universe size, and how often")
    print("        the executor emits orders (reflection adds cost).")
    print("------------------------------------------------------------")
    return est


async def main_async() -> int:
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

    # Real-LLM safety: apply the default max-days cap if the user didn't pass one.
    max_days = args.max_days
    if args.real_llm and max_days == 0:
        max_days = DEFAULT_MAX_DAYS_REAL_LLM

    warmup = 30
    if max_days > 0:
        candles = _trim_to_last_n_days(candles, max_days, warmup)

    provider = HistoricalSnapshotProvider(
        universe=symbols,
        candles_by_symbol=candles,
        warmup_candles=warmup,
    )

    if args.real_llm:
        n_days = max(0, len(provider.trading_days()))
        est = _print_cost_estimate(n_days)
        if args.estimate_only:
            return 0
        if not args.confirm:
            reply = input(f"proceed with real LLM run (~${est:.2f})? [y/N] ")
            if reply.strip().lower() not in {"y", "yes"}:
                print("aborted.")
                return 1

    engine = BacktestEngine(
        provider=provider,
        llm_client=_build_llm_client(args),
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
                "num_closed_positions": result.num_closed_positions,
                "num_lessons": result.num_lessons,
                "days": len(result.days),
                "final_equity": round(result.equity[-1], 2),
                "btc_equity": round(result.btc_equity[-1], 2),
                "final_elo_weights": {
                    k: round(v, 3) for k, v in result.final_elo_weights.items()
                },
            },
            indent=2,
        )
    )

    if args.real_llm:
        from src.llm import tracker

        print(
            f"\nActual LLM spend this run: ${tracker().cost_usd:.4f} "
            f"over {tracker().calls} calls"
        )
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
