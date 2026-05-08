# Improvements Tracker

**Living checklist** of pending improvements. Each item is tagged with priority
and (when completed) the commit that fixed it.

Priority legend:
- 🔴 **P0-bug** — outright bugs that must ship before any live use
- 🟠 **P0** — must ship before live use, but not bugs (correctness gaps)
- 🟡 **P1** — within first month of live operation
- 🟢 **P2** — long-term hardening

---

## 🔴 P0 — Bug-class issues (block live)

| # | Item | File(s) | Status |
|---|---|---|---|
| 1 | TWAP slices fire back-to-back without spacing → effectively a single order. `execution.twap_interval_seconds` defined but unused. | `agents/execution_agent.py` | ☐ |
| 2 | `get_universe()` `lru_cache` never invalidates → in a long-running scheduler the universe is frozen at boot. | `data/universe.py`, `scheduler/scheduler.py` | ☐ |
| 3 | Pyramid evaluates against the broker last price; should explicitly use the order_phase live price + document. | `portfolio/pyramid.py` | ☐ |
| 4 | APScheduler `jitter` is a `timedelta`-or-int second param — verify the cron actually applies it. | `scheduler/scheduler.py` | ☐ |

## 🟠 P0 — Correctness / coverage gaps

| # | Item | File(s) | Status |
|---|---|---|---|
| 5 | LLM responses must be contract-tested per prompt (JSON schema parsing). | `tests/test_agent_contract.py` (new) | ☐ |
| 6 | KRX business-day calendar so cron skips holidays / temporary market closures. | `common/calendar.py` (new) | ☐ |
| 7 | Partial-fill handling: today nothing follows up on a `status='partial'` execution. | `scheduler/daily_pipeline.py::monitor_unfilled_phase` | ☐ |
| 8 | Docker lint / static validation (we can't `docker build` here, but we can validate the COPY paths and entrypoints exist). | `Dockerfile`, `docker-compose.yml` | ☐ |

## 🟡 P1 — First-month operational

| # | Item | File(s) | Status |
|---|---|---|---|
| 9 | Filter trading-halt / 관리종목 / investment-warning tickers from the universe. | `data/universe.py` | ☐ |
| 10 | KIS API token-bucket rate limiter (broker free-tier hard caps requests per second). | `broker/kis_client.py` | ☐ |
| 11 | Paper-vs-live divergence monitor — when going live, run paper in parallel and alert if drift > 0.5%. | `scheduler/daily_pipeline.py`, `common/notifications.py` | ☐ |
| 12 | End-to-end integration test — research → order (mocked KIS) → EOD round-trip. | `tests/test_e2e.py` (new) | ☐ |
| 13 | Pre-commit hooks (ruff + mypy strict) so style/type gate runs before each commit. | `.pre-commit-config.yaml` (new) | ☐ |

## 🟢 P2 — Long-term hardening

| # | Item | File(s) | Status |
|---|---|---|---|
| 14 | Prompt versioning — hash each prompt + tag every journal row with that hash for attribution-by-prompt-version. | `agents/base.py`, `memory/journal.py` | ☐ |
| 15 | Streamlit "Portfolio X-ray" page — factor / sector / beta exposures of the live book. | `dashboard/app.py` | ☐ |

---

## How to use this file

When the assistant works through these items it will:
1. Move the row to **In progress** (👷),
2. Implement the fix + tests,
3. Mark it ✅ with the commit hash.

The bottom of this file gets a "Resolved" log so we can audit the change history.

## Resolved log
