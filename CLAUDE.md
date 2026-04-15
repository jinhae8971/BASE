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
├── dashboard/      # (Phase 6) Streamlit UI
└── common/         # config, logging, types, LLM client
config/
├── settings.yaml   # risk limits, universe, schedule, model choices
└── prompts/        # system prompt per agent (macro/sector/value/quant/...)
scripts/            # run_daily.py, run_backtest.py, run_reflection.py, kill_switch.py
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

## Running Locally

```bash
pip install -e ".[dev]"
cp .env.example .env   # fill in keys
python scripts/run_daily.py --env paper --dry-run
pytest
ruff check src tests
```

## Extending the System

- **Adding a new agent**: create `src/agents/<name>_agent.py` subclassing `BaseAgent`, add a prompt at `config/prompts/<name>.md`, wire it into `scheduler/daily_pipeline.py::run_daily`, and add its weight in `config/settings.yaml::consensus.weights`.
- **Adding a data source**: add a `fetch_*` function in `src/data/` returning a plain dict; don't embed secrets; handle offline fallbacks gracefully.
- **Adding an optimizer**: implement in `src/orchestrator/optimizer.py` and switch via `config/settings.yaml::optimizer.method`.

## Performance Targets (don't forget)

- **CAGR** > KOSPI + 5%p
- **MDD** ≤ 15%
- **Sharpe** ≥ 1.0
- **Information Ratio** ≥ 0.5

Any change that loosens risk limits must be justified against these targets in the PR description.

## Roadmap (where we are)

- [x] **Phase 0** — scaffold + docs
- [ ] **Phase 1** — real data pipelines + KIS paper live
- [ ] **Phase 2** — agents MVP with real prompts/context
- [ ] **Phase 3** — backtest engine
- [ ] **Phase 4** — journal + reflection + RAG
- [ ] **Phase 5** — 2-month paper run
- [ ] **Phase 6** — dashboard + alerts
- [ ] **Phase 7** — small live capital
