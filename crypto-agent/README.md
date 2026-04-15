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

**Phase 3 — Orchestrator hardening.** `DailyWorkflow` is now resilient by
default:
- **Graceful degradation** — any single agent failure falls back to that
  agent's deterministic stub so the rest of the pipeline still runs.
- **Lessons RAG injection** — the top-N most recent post-mortem lessons
  are pulled from the vector store and exposed on `AgentContext.lessons`
  so research, value, and executor prompts can reference them.
- **Run artifact** — every run writes a single structured JSON to
  `data/runs/<YYYY-MM-DD>/<run_id>.json` containing agent payloads, token
  usage, cost, allocation, orders, fills, and any errors. This is the
  audit trail and the replay input for Phase 6 Optuna.
- **Telegram alerts** — no-op if `TELEGRAM_BOT_TOKEN` is unset.
- **Async scheduler** — `src/orchestrator/scheduler.py` runs the daily
  workflow on an interval, honors the `HALT` file, survives failures, and
  is testable with an injected fake clock.

CLI: `python -m src.orchestrator.run_daily once --dry-run`
     `python -m src.orchestrator.run_daily schedule --interval-hours 24`

Phase summary:
- **P4 backtest** — SimulatedBinanceClient + metrics + HeuristicLLMClient
  baseline + HistoricalSnapshotProvider + day-by-day engine
- **P2 real LLM agents** — tool-calling, prompt caching, budget guard
- **P1 data layer** — free-tier HTTP clients + snapshot fanout
- **P0 scaffold** — agents / portfolio / risk / execution skeleton

All 53 tests run offline with zero network and zero Anthropic calls.

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
