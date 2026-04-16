# S&P 500 Research Agent

Daily S&P 500 top-gainer research agent. Runs **every US trading day after market close** and:

1. Scrapes the **S&P 500 constituent list** from Wikipedia + downloads OHLCV via **yfinance** (no API key required)
2. Persists daily snapshots to compute exact **2-trading-day price change**
3. Picks the **top 5 gainers** by 2-day return, filtering out low-volume noise
4. Fetches news via **yfinance built-in news feed** (no extra API)
5. Uses **Claude Sonnet 4.6** (with prompt caching) to analyze each gainer — catalysts, drivers, risks, GICS sector classification, confidence score
6. Synthesizes a **7-day market narrative** across prior reports (sector rotation, thematic trends, actionable PM insight)
7. Writes JSON reports to `docs/reports/` and deploys a **GitHub Pages** dashboard
8. Sends a **Telegram** summary with deep-link to the dashboard

Everything runs as a **GitHub Actions cron** — no server needed.

---

## Architecture

```
GitHub Actions (cron: 22:00 UTC ≈ 17:00 ET, weekdays)
        │
        ▼
┌─────────────┐   ┌──────────────┐   ┌──────────────────┐
│ Fetcher     │──▶│ Ranker       │──▶│ Analyzer (Claude)│──┐
│ yfinance    │   │ 2-day Top 5  │   │  + yfinance news  │  │
│ + Wikipedia │   └──────────────┘   └──────────────────┘  │
└─────────────┘                                             ▼
        │                                       ┌──────────────────┐
        ▼                                       │ Narrative (Claude)│
┌────────────────┐                               │  7-day synthesis  │
│ Snapshots      │◀── loaded by ranker ─────────└─────────┬────────┘
│ (2 trading     │                                         │
│  days ago)     │                                         ▼
└────────────────┘                          ┌─────────────────────────┐
                                            │ docs/reports/*.json     │ → GitHub Pages
                                            └─────────────────────────┘
                                                           │
                                                           ▼
                                                  ┌──────────────┐
                                                  │ Telegram Bot │
                                                  └──────────────┘
```

## Directory structure

```
.
├── src/
│   ├── main.py               # Pipeline entry point
│   ├── fetcher.py            # yfinance + Wikipedia S&P 500 list
│   ├── ranker.py             # 2-trading-day top-K selection
│   ├── news.py               # yfinance news feed
│   ├── analyzer.py           # Claude per-stock analysis (prompt caching)
│   ├── narrative.py          # Weekly narrative synthesis
│   ├── notifier.py           # Telegram MarkdownV2
│   ├── storage.py            # Snapshot + report persistence
│   ├── config.py             # Env-backed Settings
│   ├── models.py             # Pydantic schemas
│   └── logging_setup.py
├── prompts/
│   ├── analyzer_system.md    # US equity analysis prompt (GICS sectors)
│   └── narrative_system.md   # US market narrative prompt (Fed, VIX, flows)
├── data/snapshots/           # Committed daily snapshots
├── docs/                     # ── GitHub Pages root ──
│   ├── index.html            # Dashboard
│   ├── report.html           # Per-date report view
│   ├── assets/{app.js, style.css}
│   └── reports/
├── tests/
├── .github/workflows/daily.yml
├── .env.example
└── pyproject.toml
```

## Setup (one-time)

### 1. Create a new GitHub repository

```bash
# on github.com, create empty repo: <your-user>/sp500-research-agent
```

### 2. Migrate this code into it

```bash
cd sp500-research-agent
git init -b main
git add .
git commit -m "Initial import: S&P 500 research agent"
git remote add origin https://github.com/<your-user>/sp500-research-agent.git
git push -u origin main
```

### 3. Enable GitHub Pages

Repository → **Settings** → **Pages** → Source: **GitHub Actions**.

### 4. Configure secrets

Repository → **Settings** → **Secrets and variables** → **Actions**.

| Scope | Name | Required | Value |
|---|---|---|---|
| Secret | `ANTHROPIC_API_KEY` | ✅ | `sk-ant-…` |
| Secret | `TELEGRAM_BOT_TOKEN` | ✅ | from @BotFather |
| Secret | `TELEGRAM_CHAT_ID` | ✅ | your chat id |
| Variable | `DASHBOARD_URL` | ✅ | `https://<user>.github.io/sp500-research-agent/` |

**No data API keys needed** — yfinance and Wikipedia are both free and keyless.

### 5. First run

Actions → **Daily S&P 500 Research** → **Run workflow**.

The first run has no prior snapshot, so the ranker falls back to **1-day** change. From day 3 onward, exact 2-trading-day change is used.

## Running locally

```bash
pip install -e ".[dev]"
cp .env.example .env     # fill secrets

# dry run: fetch + rank only (no LLM, no telegram)
python -m src.main --dry-run

# full run, skip telegram
python -m src.main --skip-telegram

# full run
python -m src.main
```

Tests + lint:

```bash
python -m pytest
python -m ruff check src tests
```

Dashboard preview:

```bash
python -m http.server --directory docs 8000
# open http://localhost:8000
```

## Cron schedule

- **22:00 UTC** ≈ **17:00 ET** (after regular US market close)
- **Weekdays only**: `0 22 * * 0-4` (Sun–Thu UTC = Mon–Fri ET)
- GitHub Actions schedule can drift a few minutes; this is expected

## Cost

- **yfinance**: Free, no API key
- **Wikipedia**: Free
- **Claude Sonnet 4.6**: ~2 calls/day (analyzer + narrative). Prompt caching
  applied. Expected: **~$0.03–0.08/day**

## Tuning (env vars)

| Variable | Default | Description |
|---|---|---|
| `TOP_K_GAINERS` | `5` | Number of top gainers |
| `MIN_VOLUME_USD` | `10000000` | Min daily trading value (USD) |
| `LOOKBACK_TRADING_DAYS` | `2` | Days to compare |
| `NARRATIVE_LOOKBACK_DAYS` | `7` | Reports for narrative |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Claude model to use |
