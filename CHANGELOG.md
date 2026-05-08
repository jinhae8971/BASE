# Changelog

All chunks documented chronologically with their commit hashes. Each entry
explains *what* changed and *why*. Restoring an old behaviour is as simple as
``git revert <hash>``.

Format: `<commit> <date-ish> — <theme>`

---

## [2026-05-08] UX polish round 2 — `10e1582`

User-experience pain points that surfaced after first deployment.

- **Empty-state CTAs**: Overview / Today / Journal pages render an "▶ 지금
  첫 리서치 실행" primary button when no data yet, instead of a vague info.
- **Friendly errors**: `_ERROR_HINTS` dict translates common exceptions
  (KIS_APP_KEY missing, RetryError, pykrx not installed, ...) into Korean
  actionable hints. Raw exception kept inside a collapsible expander.
- **Last-updated indicator** on every page footer.
- **Journal filters**: 3-column Agent / Action / Ticker-keyword + CSV
  download (utf-8-sig BOM for Excel).
- **Slider tooltips**: every Control panel slider has a Korean `help=`.
- **Notification deeplink**: `notifications.dashboard_url` auto-appends
  "대시보드 열기" link to every Slack/Telegram message.
- **Next-cron countdown** in sidebar: "다음 [리서치] 까지 1시간 23분".
- Sidebar block refactored into `_render_sidebar()` to fix module-load
  order issue.

Tests: 179 pass + 1 skipped.

---

## [2026-05-08] Dashboard QA — `ac66c47`

Audit of all 10 dashboard pages.

- **Bug**: Reflection "Strip patches block" regex was non-greedy on the
  opening fence, leaving the closing fence + JSON content. Fixed to match
  through end-of-document.
- **Dead code**: removed `_t.sleep(0)` placeholder.
- **Doc drift**: module docstring updated to reflect 10 pages + Quick
  actions.
- **UX**: Quick action spinners now state wall-clock estimates ("60-120s
  for research") + caption warning that the tab blocks.
- **Tests**: 9 new dashboard tests under a Streamlit stub (imports, page
  surface, regex round-trip, dotted setters, doc-drift detector,
  whitelist alignment).

---

## [2026-05-08] Dashboard becomes the operator's primary surface — `b59b584`

Five new pages so an operator can do the full daily loop without the CLI.

- **☀️ Today**: target-of-the-day, top-25 weights, regime hint, imminent
  stop signals, "Send morning report now" button.
- **🚨 Alerts**: 7-day filter on guard / stop / replace / param-update
  events with per-agent action mix bar chart.
- **🤖 Learning**: TWAP bandit arm rewards, booster per-agent
  attribution, A/B paper book NAV curves + Sharpe table.
- **🎛️ Control**: every feature flag as a toggle + risk-param sliders +
  excluded-tickers editor + **Kill switch** (typed-confirm gated).
- **🪞 Reflection**: read latest report + "Apply patches now" button.

---

## [2026-05-08] UX overhaul — `9bf6cfb`

- **mais unified CLI** (`src/cli.py`): status / positions / today /
  doctor / logs / backup / restore / set / flag / run / init.
- **Init wizard** (`mais init`): walks operator through .env and runs
  doctor.
- **Notification categories + digest + rate limit**: per-category
  toggles, hourly digest mode, in-process rate limiter.
- **Streamlit polish**: auto-refresh, Quick action buttons, NAV metrics,
  feature flag badges, color-shaded position table.

---

## [2026-05-08] Future ideas chunk 11 — `751a2e9`

Five new directions completed.

- **F-5 Universe**: KOSPI200 / KRX300 / KOSDAQ150 / UNION via
  `INDEX_CODES`.
- **F-1 Global news**: 6 free English RSS feeds fed into MacroAgent.
- **F-3 Defensive hedge**: spot-only ETF basket (411060 / 304660 /
  132030); long-only, no futures.
- **F-4 ε-greedy bandit**: TWAP interval auto-tuned daily by slippage.
- **F-2 Deep MLP booster tier**: auto-promotes when n≥50 picks
  available; falls back to ridge or mean.

---

## [2026-05-07] Remaining ideas chunks 7–10 — `ce9b1c4` / `8feb138` / `ac56453` / `21bcd22`

- **R-1 Prometheus metrics** at `/metrics` (port 9100).
- **R-6 52-week breakout backtest regression**.
- **R-3 Reflection auto-apply** (whitelisted parameter changes).
- **R-2 A/B optimizer paper books** (score_weighted vs MV vs BL
  parallel run, monthly Sharpe winner).
- **R-4 ML alpha booster** (sklearn ridge on attribution → consensus
  weight nudges).
- **R-5 KIS realtime websocket** stops (H0STCNT0 stream + 1.5%
  threshold).

---

## [2026-05-07] Improvements chunks 1–6 — `6bfd828` … `7cee5ec`

- **TWAP slice spacing** (was firing back-to-back).
- **Universe cache invalidate** at EOD.
- **Pyramid price source documented**.
- **Scheduler jitter sanity test**.
- **Agent contract tests** for parsing.
- **KRX business calendar** (cron skips holidays).
- **Partial-fill reconciliation** with KIS truth.
- **Docker static validation** + halted-ticker filter.
- **KIS API token-bucket rate limiter**.
- **Paper-vs-live divergence monitor** + E2E smoke test.
- **Pre-commit hooks** (ruff + mypy + safety).
- **Prompt versioning** + journal column.
- **Streamlit X-ray page** (factor / sector / pyramid exposure).

---

## [2026-05-06] Trader-grade upgrades — `e6f58e9`

- **52-week breakout factor**.
- **Overnight shock gate** (EWY proxy).
- **Pyramid (let winners run+)** — +20% / +40% auto-add.
- **Unfilled order monitor + replace** at fresh bid/ask.

---

## [2026-05-06] Intraday stops — `68c8354`

30-minute cron (10:00–15:00 KST) defends capital between daily order
phases.

---

## [2026-05-06] Bull-market momentum strategy — `2b23739`

Aligned to user request: long-only, no futures, "가는 말에 타오른다".

- **Regime-conditional factor weights**: risk_on M=0.45 V=0.15 ...
- **Asymmetric stops**: -12% hard / -10% trailing.
- **Asymmetric drift**: BUY 4% / SELL 8%.
- **Graduated MDD**: 10/12/15 → trim 25/50/75%.
- **Flow factor**: foreign + institutional 5d net-buy.
- **TWAP slicing + scheduler jitter**.
- **Daily morning report + heartbeat**.
- Consensus weights tilted: quant 0.45.

---

## [2026-05-06] Trader-grade hardening — `fead679`

Fix "shape only" gaps surfaced by the audit.

- **DailyRiskGuard** enforces daily_loss_kill / mdd_trigger /
  turnover_cap (declared but never enforced before).
- **KRX tick-size snapping** (mandatory for live KIS orders).
- **Slack + Telegram notifications**.
- **Quote-aware limit pricing** (bid/ask, fall back ±30bps offset).
- **ADV liquidity cap**.
- **Phase split**: research_phase ↔ order_phase ↔ eod_phase.
- **Information ratio / tracking error / α / β**.
- **Rotating file logs**.
- **kill_switch** uses parsed `get_account_state`.

---

## [2026-05-04] Phase 1–6 implementation — `9fa1ecf`

Replaces the Phase 0 stubs with real implementations.

- **Data layer**: pykrx universe, market panel, fundamentals, ECOS
  macro, RSS news.
- **KIS broker**: full account_state parsing, bulk quotes, cancel order.
- **Optimizer**: PyPortfolioOpt mean-variance + Black-Litterman.
- **Backtest**: event-driven, slippage + commission + tax model, KOSPI
  benchmark + alpha.
- **Memory**: outcome backfill, Chroma RAG.
- **Streamlit dashboard** (Overview / Positions / Journal / Backtest
  initial).
- **Docker** Dockerfile + compose + Windows guide.

---

## [2026-05-04] Phase 0 scaffold — `4347a0d`

Initial repo skeleton: agents.base, common.types, orchestrator
consensus + score-weighted optimizer, SQLite journal, settings.yaml,
prompts, CI workflow.
