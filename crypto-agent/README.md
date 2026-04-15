# crypto-agent

Multi-agent cryptocurrency auto-trading system built on the Claude Agent SDK.

## Mission

- **Primary KPI**: Beat BTC HODL on a rolling 3-month basis (Alpha > 0).
- **Risk KPI**: Max Drawdown < 15% (hard cap 25% → full circuit break).
- **Initial capital**: 1,000 USDT on Binance Spot.
- **Data**: Free-tier only (CoinGecko, DefiLlama, CryptoPanic, FRED, Binance public).

## Six Agents

| # | Agent | Model | Role |
|---|---|---|---|
| 1 | Crypto Research | Sonnet 4.6 | News/on-chain/narrative sentiment |
| 2 | Macro | Sonnet 4.6 | DXY/rates/equities regime |
| 3 | Sector | Sonnet 4.6 | L1/L2/DeFi/AI rotation |
| 4 | Value Investor | Opus 4.6 | Tokenomics & fair value |
| 5 | Crypto Quant | Sonnet 4.6 | Price/volatility signals |
| 6 | Executor | Opus 4.6 | Portfolio construction & orders |

A seventh **Reflection** agent runs after each closed trade and updates the ELO-weighted signal aggregator.

## Architecture

```
Daily cron ─► Orchestrator ─► [6 Agents] ─► Aggregator ─► Optimizer ─► Risk
                                                                        │
                                                                        ▼
                                                              Binance Executor
                                                                        │
                                                                        ▼
                                                       Trade Journal (Postgres)
                                                                        │
                                                                        ▼
                                                   Reflection ─► ELO ─► (next run)
```

See `../ULTRA_PLAN.md` for the full design and phased roadmap.

## Status

**Phase 4 — Backtest engine.** The `src/backtest/` package drives
`DailyWorkflow` over historical data day-by-day through a
`SimulatedBinanceClient` that supports realistic fees and slippage.
`HistoricalSnapshotProvider` builds an as-of `MarketSnapshot` for any date
in the candle archive. `HeuristicLLMClient` — a deterministic baseline that
implements the `LLMClient` protocol — lets backtests run offline without
spending Anthropic credits, and acts as a minimum bar the real agents must
beat. Performance reports include total return, CAGR, Sharpe, Sortino,
MDD, win rate, profit factor, turnover, and the primary KPI: **Alpha vs
BTC HODL**.

CLI: `python -m scripts.run_backtest --days 180 --btc-drift 0.002`

Prior phases:
- **P2 real LLM agents** — 7 agents with tool-calling, prompt caching,
  budget guard
- **P1 data layer** — free-tier HTTP clients + snapshot fanout
- **P0 scaffold** — agents/portfolio/risk/execution skeleton

All 46 tests run offline with zero network and zero Anthropic calls.

## Getting started (dev)

```bash
cp .env.example .env
docker compose up -d postgres redis qdrant
uv sync   # or: pip install -e .
pytest
python -m src.orchestrator.run_daily --dry-run
```

## Safety

- `TRADING_MODE` defaults to `dry`. Only `live` places real Binance orders.
- Binance API keys MUST have **Withdraw OFF** and **Futures OFF**.
- A file named `HALT` at the repo root halts all trading on the next tick.
- MDD circuit breaker at 15% flattens to cash and requires human ack.
