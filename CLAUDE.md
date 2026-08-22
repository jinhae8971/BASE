# CLAUDE.md — Guidance for Claude Code

This file gives Claude Code context about the **Multi-Agent Investment System (MAIS)** so that agents working in this repo produce correct, consistent changes.

## Project Summary

MAIS is a Korean-equity automated trading system where five LLM-driven specialist agents (Macro, Sector, Value, Quant, Execution) cooperate daily to manage a KOSPI-benchmarked portfolio via the 한국투자증권 (KIS) Open API. The system targets **alpha vs. KOSPI** while capping **MDD at 15%**, and learns from its own decision history through a Reflection + RAG loop.

## Repository Layout

```
src/
├── agents/         # 5 specialists + reflection; all subclass BaseAgent
├── orchestrator/   # consensus voting + portfolio optimizer
├── broker/         # KIS Open API wrapper (paper/live)
├── data/           # market / fundamentals / macro / news ingestion
├── portfolio/      # risk metrics, position sizing
├── backtest/       # event-driven backtest engine
├── memory/         # SQLite decision journal + Chroma RAG
├── scheduler/      # daily_pipeline.py entry point
├── upbit/          # Upbit crypto day-trading subsystem (independent of the equity stack)
├── dashboard/      # FastAPI server + static SPA (Upbit trading desk)
└── common/         # config, logging, types, LLM client
config/
├── settings.yaml   # risk limits, universe, schedule, model choices, `upbit:` section
└── prompts/        # system prompt per agent (macro/sector/value/quant/...)
scripts/            # run_daily.py, run_backtest.py, run_reflection.py, kill_switch.py,
                    # run_upbit_dashboard.py, run_upbit_daily.py, upbit_kill_switch.py
tests/              # pytest unit + integration tests
docs/               # architecture.md, agents.md, risk_policy.md
```

## Conventions

- **Python 3.11**, type hints everywhere, `from __future__ import annotations` at the top of every module.
- **Ruff** for lint/format, **mypy** for types, **pytest** for tests.
- Agents produce `AgentProposal` (see `src/common/types.py`); orchestrator produces `PortfolioTarget`; broker accepts `Order` and returns `ExecutionResult`.
- No secrets in code. Secrets live in `.env`, loaded via `pydantic-settings` in `common/config.py`.
- Settings lookup via `get_setting("risk.max_position_weight")` rather than hard-coded numbers.
- Logging via `common.logging.get_logger(__name__)` — structured logs only.

## Safety Rules (non-negotiable)

1. **Never** place a live order from tests or examples — always `dry_run=True` unless explicitly invoked via `scripts/run_daily.py --live`.
2. **Never** commit `.env`, API keys, or `data_store/`.
3. **Never** edit a Reflection proposal directly into prompts/config. Reflection writes Markdown to `data_store/reflections/`; a human applies it after review.
4. **Never** bypass the risk guards in `ExecutionAgent._apply_risk_guards`.
5. The **kill switch** (`scripts/kill_switch.py`) is the only path to liquidate all positions; it requires explicit `--confirm I-UNDERSTAND`.
6. Live-trading work defaults to `KIS_ENV=paper`. Changing to `live` must be an explicit user request.
7. **Upbit**: the engine defaults to `mode: paper`. Never construct `LiveBroker` or call
   `UpbitClient.place_order` from tests or examples — tests use `PaperBroker` with a fake client.
8. **Upbit long-term holdings are inviolable.** Coins in `long_term_holdings` must never be bought
   or sold by the engine. Every sell is capped by `HoldingsGuard.tradable_quantity`; do not add a
   code path that bypasses it, including in liquidation and EOD flatten.
9. Upbit API keys are entered in the dashboard and sealed under `data_store/`. Never log them,
   never return them unmasked from the API, never write them to `config/` or a committed file.

## Running Locally

```bash
# Dependencies are split per subsystem — the base install is the shared pure-Python
# core, and each stack adds its own extra.
pip install -e ".[equity,dev]"   # Korean equity stack (KIS, DART, optimizer, RAG)
pip install -e ".[upbit,dev]"    # Upbit crypto stack (FastAPI dashboard, JWT, crypto)

cp .env.example .env   # fill in keys
python scripts/run_daily.py --env paper --dry-run
pytest
ruff check src tests
```

The Upbit subsystem runs standalone — `./start-upbit.sh` (or `start-upbit.ps1` on
Windows) creates the venv, installs `.[upbit]`, runs `mais-upbit doctor`, and serves
the dashboard. `mais-upbit doctor` is the preflight: deps, settings, storage,
credentials, public/private API reachability, and the dashboard port.

## Extending the System

- **Adding a new agent**: create `src/agents/<name>_agent.py` subclassing `BaseAgent`, add a prompt at `config/prompts/<name>.md`, wire it into `scheduler/daily_pipeline.py::run_daily`, and add its weight in `config/settings.yaml::consensus.weights`.
- **Adding a data source**: add a `fetch_*` function in `src/data/` returning a plain dict; don't embed secrets; handle offline fallbacks gracefully.
- **Adding an optimizer**: implement in `src/orchestrator/optimizer.py` and switch via `config/settings.yaml::optimizer.method`.
- **Changing Upbit scoring**: add the metric in `src/upbit/indicators.py`, fold it into the relevant
  pillar in `src/upbit/scoring.py`, and put the raw value in `ScoreBreakdown.metrics` so the
  dashboard's 분석내역 tab can show it. Add a Korean label to `METRIC_LABELS` in
  `src/dashboard/static/app.js`.
- **Adding an Upbit strategy knob**: add the field to the right model in `src/upbit/strategy.py`,
  the default to `config/settings.yaml::upbit`, and a row to `FIELD_DEFS` in `app.js`.

## Performance Targets (don't forget)

- **CAGR** > KOSPI + 5%p
- **MDD** ≤ 15%
- **Sharpe** ≥ 1.0
- **Information Ratio** ≥ 0.5

Any change that loosens risk limits must be justified against these targets in the PR description.

## Upbit Subsystem (crypto)

Separate from the equity stack: KRW-market alt day trading, scanned daily at 09:10 KST.

- Config: `config/settings.yaml::upbit` is the baseline; dashboard edits are stored as a
  deep-merge patch in `app_settings.config` and win. Read it with `upbit.strategy.load_config()`,
  never with `get_setting()` directly.
- Score = 거래량 · 수급 · 차트 · 베타, each 0-100, blended by `upbit.scoring.weights`.
  BTC regime (`scoring.assess_regime`) gates exposure; `risk_off` means zero new entries.
- Persistence is one SQLite file (`upbit.store.UpbitStore`); the dashboard reads only from it.
- `TradingEngine.reload()` re-reads config and holdings each cycle, but never replaces an
  explicitly injected config or broker — that is what makes the engine testable.
- Tests must stay offline: use `tests/upbit_fakes.FakeUpbitClient`, whose `get_accounts()` raises.

Docs: `docs/upbit_trading.md`.

## Roadmap (where we are)

- [x] **Phase 0** — scaffold + docs
- [ ] **Phase 1** — real data pipelines + KIS paper live
- [ ] **Phase 2** — agents MVP with real prompts/context
- [ ] **Phase 3** — backtest engine
- [ ] **Phase 4** — journal + reflection + RAG
- [ ] **Phase 5** — 2-month paper run
- [ ] **Phase 6** — dashboard + alerts
- [ ] **Phase 7** — small live capital

### Upbit track (independent)

- [x] **U0** — client, scoring, engine, scheduler, dashboard, paper mode
- [ ] **U1** — paper-run validation over a full cycle, parameter tuning
- [ ] **U2** — backtest/replay harness for the scoring model
- [ ] **U3** — alerts (Slack/Telegram) on entries, exits and kill-switch events
- [ ] **U4** — small live capital
