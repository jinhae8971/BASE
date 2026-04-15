# Architecture

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│                  Daily Scheduler (08:00 KST)                │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Data Ingestion Layer                     │
│  KIS API · DART · ECOS · FRED · pykrx · FDR · News RSS      │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                   Specialist Agents (LLM)                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │  Macro   │  │  Sector  │  │  Value   │  │  Quant   │     │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘     │
│       └─────────────┴──────┬──────┴─────────────┘           │
│                            ▼                                │
│                 AgentProposal objects                       │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              Orchestrator (Consensus + Optimizer)           │
│  • Conviction-weighted voting                               │
│  • Black-Litterman / Mean-Variance / Risk-Parity            │
│  • Sector/position caps, cash buffer                        │
└───────────────────────────┬─────────────────────────────────┘
                            ▼ PortfolioTarget
┌─────────────────────────────────────────────────────────────┐
│           Execution Agent (Risk Guard + KIS Order)          │
│  • Rebalance threshold                                      │
│  • Position/sector/cash caps                                │
│  • Order planning (limit/TWAP)                              │
│  • Kill Switch hook                                         │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│        Decision Journal (SQLite) + RAG Memory (Chroma)      │
└───────────────────────────┬─────────────────────────────────┘
                            ▼  (weekly)
┌─────────────────────────────────────────────────────────────┐
│    Reflection Agent — attribution, failure clustering,     │
│      prompt/param improvement proposals → human review     │
└─────────────────────────────────────────────────────────────┘
```

## Data Flow (one trading day)

1. **08:00 KST** — APScheduler fires `run_daily()`
2. Each of the 4 research agents independently calls `gather_context(as_of)` → LLM → `AgentProposal`
3. `Consensus.aggregate()` produces `{equity_weight, regime, sector_tilts, ticker_scores}`
4. `PortfolioOptimizer.optimize()` returns a `PortfolioTarget`
5. `ExecutionAgent.execute()` plans orders, applies risk guards, and submits to KIS (or dry-run)
6. Every decision is persisted to the Decision Journal
7. **16:00 KST** — EOD review computes day's P&L, attributes to each agent
8. **Friday 18:00 KST** — Reflection agent writes a weekly report

## Key Design Decisions

- **Agents are pure functions of (prompt, context) → proposal.** This makes them easy to unit-test and replay against any historical date.
- **Consensus is conviction-weighted**, so confident agents dominate but weak signals still leak through.
- **Optimizer is pluggable** (`config/settings.yaml::optimizer.method`) so we can A/B mean-variance vs. Black-Litterman vs. risk-parity without touching code paths.
- **Execution is deterministic.** The LLM's job ends at the orchestrator; execution is a pure risk-gated order planner so we can sleep at night.
- **Everything is journaled.** Our edge depends on being able to backtest against our own history.
