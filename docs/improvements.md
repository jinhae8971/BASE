# Improvements Tracker

**Living checklist** of improvement work. Closed items are stamped with the
commit that fixed them.

Priority legend:
- 🔴 **P0-bug** — outright bugs that must ship before any live use
- 🟠 **P0** — must ship before live use, but not bugs (correctness gaps)
- 🟡 **P1** — within first month of live operation
- 🟢 **P2** — long-term hardening

---

## 🔴 P0 — Bug-class issues (block live)

| # | Item | Status | Commit |
|---|---|---|---|
| 1 | TWAP slices fire back-to-back without spacing | ✅ | `6bfd828` |
| 2 | `get_universe()` lru_cache never invalidates | ✅ | `6bfd828` |
| 3 | Pyramid evaluates against unspecified price source | ✅ | `6bfd828` |
| 4 | APScheduler jitter param verification | ✅ | `6bfd828` |

## 🟠 P0 — Correctness / coverage gaps

| # | Item | Status | Commit |
|---|---|---|---|
| 5 | LLM responses must be contract-tested per prompt | ✅ | `6bfd828` |
| 6 | KRX business-day calendar so cron skips holidays | ✅ | `6bfd828` |
| 7 | Partial-fill reconciliation with KIS truth | ✅ | `6bfd828` |
| 8 | Docker lint / static validation | ✅ | `b575a30` |

## 🟡 P1 — First-month operational

| # | Item | Status | Commit |
|---|---|---|---|
| 9 | Filter trading-halt / 관리종목 / 투자경고 tickers | ✅ | `b575a30` |
| 10 | KIS API token-bucket rate limiter | ✅ | `ec6b9c6` |
| 11 | Paper-vs-live divergence monitor | ✅ | `5915f7e` |
| 12 | End-to-end integration test | ✅ | `5915f7e` |
| 13 | Pre-commit hooks (ruff + mypy + safety) | ✅ | `44cb9dd` |

## 🟢 P2 — Long-term hardening

| # | Item | Status | Commit |
|---|---|---|---|
| 14 | Prompt versioning + journal column | ✅ | `44cb9dd` |
| 15 | Streamlit "Portfolio X-ray" page | ✅ | (this chunk) |

---

## How to use this file

When the assistant works through these items it:
1. Marks the row **In progress** (👷),
2. Implements the fix + tests,
3. Stamps it ✅ with the commit hash.

## Resolved log

- `6bfd828` chunk 1 — TWAP spacing, universe cache invalidate, pyramid price
  doc, jitter test, agent contract tests, KRX calendar, partial-fill
  reconciliation
- `b575a30` chunk 2 — Docker static validation, halted/admin filter
- `ec6b9c6` chunk 3 — KIS API token-bucket rate limiter
- `5915f7e` chunk 4 — paper-vs-live divergence monitor, E2E smoke test
- `44cb9dd` chunk 5 — pre-commit hooks, prompt versioning + journal column
- `7cee5ec` chunk 6 — Streamlit X-ray page
- `ce9b1c4` chunk 7 — Prometheus metrics + 52w breakout regression (R-1, R-6)
- `8feb138` chunk 8 — Reflection auto-apply + A/B optimizer paper books (R-3, R-2)
- `ac56453` chunk 9 — ML alpha booster (R-4)
- (this) chunk 10 — KIS websocket realtime stops (R-5)

## Remaining ideas — all addressed ✅

| # | Idea | Status | Commit |
|---|---|---|---|
| R-1 | API watchdog — Prometheus exporter | ✅ | `ce9b1c4` |
| R-2 | A/B optimizer paper books | ✅ | `8feb138` |
| R-3 | Reflection auto-application (whitelisted) | ✅ | `8feb138` |
| R-4 | ML alpha booster (sklearn ridge) | ✅ | `ac56453` |
| R-5 | Real-time websocket stops (H0STCNT0) | ✅ | (this) |
| R-6 | 52-week breakout backtest regression | ✅ | `ce9b1c4` |

## Future ideas — not yet promoted

The above closes the initial improvement sweep. Genuinely new ideas to
consider as the system runs:

- **Multi-language news** (Bloomberg / Reuters API) for non-Korean macro
- **Deep learning alpha booster** (LSTM on prompt-versioned outcome history)
  if the ridge booster's predictive power plateaus
- **Cross-asset hedge** (gold / KRW future) — currently long-only equity,
  exposed to broad-market drawdowns
- **Reinforcement learning for execution** (TWAP slice scheduling)
- **Korean small-cap universe** beyond KOSPI200 (KRX300 + KOSDAQ150) once
  liquidity / data quality is validated
