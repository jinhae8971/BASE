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

**Phase 6 — Self-learning loop activated.** The system now actually
changes its own behavior as a function of realized outcomes, which is the
core user requirement. New pieces:

- `src/learning/position_tracker.py` — observes every fill, maintains
  per-symbol open-position state with weighted-average entry price and
  first-entry-wins context, emits a `ClosedPosition` when a SELL
  fully unwinds a position.
- `src/learning/learning_loop.py` — consumes `ClosedPosition` events,
  runs the `ReflectionAgent` post-mortem, persists the natural-language
  lesson into the vector store (so next run's RAG retrieval surfaces
  it), and updates the shared `EloTable` with per-agent scoring deltas.
- `DailyWorkflow` — wires the tracker onto every fill and runs the
  learning loop on every close at end-of-run. Errors are logged but
  never block the workflow.
- `BacktestEngine` — now owns a *persistent* `EloTable`, lesson store,
  `PositionTracker`, and `LearningLoop` across all days so the feedback
  loop actually evolves over a multi-day run.

**Heuristic baseline breakthrough.** With the learning loop closed, the
heuristic `HeuristicLLMClient` on a sinusoidal 200-day synthetic market
beats BTC HODL for the first time: `Return +25.9%` vs `BTC +25.26%`,
`Alpha +0.64pp`, `MDD 5.51%`, `Sharpe 5.86`. ELO drifts from uniform to
`value 0.30 / research 0.26 / sector 0.17 / macro 0.17 / quant 0.10`.
That's a concrete "system learns" existence proof — the real LLM
agents should beat this by a wider margin once wired in.

Prior phases:
- **P3 orchestrator hardening** — graceful degradation, RAG injection,
  run artifacts, scheduler, Telegram alerts
- **P4 backtest** — portfolio sim, metrics, historical provider
- **P2 real LLM agents** — tool-calling, prompt caching, budget guard
- **P1 data layer** — free-tier HTTP clients + snapshot fanout
- **P0 scaffold** — agents / portfolio / risk / execution skeleton

CLI: `python -m src.orchestrator.run_daily once --dry-run`
     `python -m scripts.run_backtest --days 200 --btc-drift 0.001`

All 63 tests run offline with zero network and zero Anthropic calls.

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
