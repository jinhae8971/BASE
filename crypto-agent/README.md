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

**Phase 5 + 7 — Paper and Live trading wired with 3-lock safety gate.**
Authenticated Binance REST (HMAC-SHA256 signed) for both testnet
(paper) and prod (live) share one implementation; the only difference
is the base URL and API keys. Live mode is locked behind three
independent affirmative conditions (env var × 2 + a grep-able ack
file) AND two hard caps on per-order and total equity exposure, AND an
automatic `canWithdraw == False` verification on the first account
call. See the Safety section below for the complete runbook.

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

All 126 tests run offline with zero network and zero Anthropic calls.

### Operational tooling

- `scripts/fetch_history.py` — bulk-download historical Binance klines
  (1000/request, auto-paginated) into `data/history/<interval>/<symbol>.ndjson`.
- `scripts/run_backtest.py --from-archive` — run the backtest engine
  over real historical data instead of synthetic drift.
- `scripts/verify_anthropic.py` — manual smoke test that runs the six
  signal agents once against the real Anthropic API (requires
  `ANTHROPIC_API_KEY`) and prints per-agent tokens, cache hit, and cost.
  The test suite never runs this — it's a pre-promotion sanity check.
- `src/execution/binance_user_stream.py` — WebSocket userDataStream
  client (listenKey lifecycle + keepalive + execution-report parser).
  Ready for Phase 5.5 real-time fill integration; tests drive it
  through an in-memory `FakeWsTransport` so the `websockets` package is
  not a hard dependency.
- `python -m src.dashboard.generator` — zero-dependency static HTML
  dashboard that walks `data/runs/` artifacts and writes a single
  self-contained `data/dashboard/index.html` with equity curve
  (hand-rolled SVG), ELO bars, recent-runs table, and error list. No
  Streamlit/server — open the file in a browser.
- `src/memory/tax_export.py` — Korean 기타소득 export with weighted-
  average cost basis, per-trade JSON + CSV, annual aggregate with
  configurable deduction (default ₩2.5M) and tax rate (default 22%).
- `Dockerfile` + `.dockerignore` — multi-stage build into an
  unprivileged runtime image. Runs `once --dry-run` by default; point
  it at `schedule` with env vars for paper/live operation.

## Getting started (dev)

```bash
cp .env.example .env
docker compose up -d postgres redis qdrant
uv sync   # or: pip install -e .
pytest
python -m src.orchestrator.run_daily --dry-run
```

## Safety

The system defends against three distinct failure modes:

1. **Accidental live execution** — someone flips `TRADING_MODE=live`
   without intending to. Three independent locks must all be present.
2. **Bug-induced over-sizing** — the executor agent hallucinates a
   massive order. Two hard caps clamp what can actually reach Binance.
3. **Key compromise** — the API key with trading rights has Withdraw
   enabled. The system refuses to trade and requires key rotation.

### Mode promotion

`TRADING_MODE` values: `dry` → `paper` → `live`. Promotion direction is
one-way intent:

- **dry**: no network, no LLM cost, no Binance. Default everywhere.
- **paper**: authenticated REST against `testnet.binance.vision`. Real
  API path, fake money. Requires `BINANCE_API_KEY` + `BINANCE_API_SECRET`
  from the testnet. This is the **Phase 5 gate** — run for 2 weeks
  before considering `live`.
- **live**: authenticated REST against `api.binance.com`. Real money.
  **Requires ALL THREE**:
  1. `TRADING_MODE=live` in the environment
  2. `PROMOTE_LIVE=1` in the environment
  3. A `.live-promotion-ack` file at the repo root containing exactly
     the string `I UNDERSTAND THE RISK OF LIVE TRADING`

Any live submit without all three → `LiveNotPromoted` raised before
leaving the process.

### Hard caps (live mode only)

- `LIVE_MAX_CAPITAL_USDT` (default **$1,000**): the system clamps
  reported account equity to this value before any sizing math, so it
  physically cannot construct an order against funds you didn't opt in.
- `LIVE_MAX_ORDER_USDT` (default **$250**): every single order's
  notional is clamped to this ceiling regardless of what the executor
  agent requests.

### API key checklist

Before running paper, and again before promoting to live:

- [ ] **Withdraw: OFF** (the system verifies this on first account call
      and refuses to trade if enabled)
- [ ] Futures: OFF
- [ ] Margin: OFF
- [ ] IP whitelist configured to the host that will run the scheduler
- [ ] Secret stored in `.env` (never committed — `.env` is gitignored)
- [ ] Telegram bot token set for failure alerts

### Kill switches

- `HALT` file at repo root → every order submission refuses until the
  file is removed (checked in both dry and live paths).
- MDD circuit breaker at 15% equity drawdown → flattens to cash and
  requires human acknowledgment before resuming.
- Daily loss > 3% → freezes new buys for the remainder of the day.
- Weekly loss > 7% → halves every open position.

### Promotion runbook

```bash
# --- Paper (Phase 5) ---
export BINANCE_API_KEY=...        # testnet key
export BINANCE_API_SECRET=...     # testnet secret
export TRADING_MODE=paper
python -m src.orchestrator.run_daily once

# --- Live (Phase 7) — only after Phase 5 passes ---
echo "I UNDERSTAND THE RISK OF LIVE TRADING" > .live-promotion-ack
export BINANCE_API_KEY=...        # real key, Withdraw OFF
export BINANCE_API_SECRET=...
export TRADING_MODE=live
export PROMOTE_LIVE=1
python -m src.orchestrator.run_daily once
```

The three-lock design means `.live-promotion-ack` is a grep-able shell-
history artifact, `PROMOTE_LIVE=1` is an env-var audit trail, and
`TRADING_MODE=live` is the obvious knob. Missing any one → no live
orders leave the process.
