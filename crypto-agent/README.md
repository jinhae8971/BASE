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

**Phase 2 — Real LLM agents.** The seven agents now route through a narrow
`LLMClient` interface:

- `AnthropicLLMClient` (prod): tool-calling with forced `tool_choice` for
  structured JSON, ephemeral prompt caching on system blocks, per-call cost
  estimation, and a shared `CostTracker` that enforces
  `ANTHROPIC_DAILY_BUDGET_USD`.
- `MockLLMClient` (tests): per-tool-name canned handlers, same contract.

Prompts live as plain markdown under `src/prompts/*.md` and are loaded once
per process. Each agent declares a JSON tool schema that doubles as the
output validator.

Dry mode with no injected client still uses the Phase 0 stubs so wiring
tests stay hermetic. All 36 tests run offline with zero network and zero
Anthropic calls.

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
