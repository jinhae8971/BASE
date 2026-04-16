# NASDAQ-100 Research Agent

Daily NASDAQ-100 top-gainer research agent. Runs **every US trading day after market close** and:

1. Scrapes the **NASDAQ-100 constituent list** from Wikipedia + downloads OHLCV via **yfinance** (no API key required)
2. Persists daily snapshots to compute exact **2-trading-day price change**
3. Picks the **top 5 gainers** by 2-day return, filtering out low-volume noise
4. Fetches news via **yfinance built-in news feed**
5. Uses **Claude Sonnet 4.6** (with prompt caching) to analyze each gainer — catalysts, GICS sector, Mag-7/AI cycle context, confidence score
6. Synthesizes a **7-day market narrative** (sector rotation, tech themes, macro sensitivity)
7. Writes JSON reports to `docs/reports/` and deploys a **GitHub Pages** dashboard
8. Sends a **Telegram** summary with deep-link

Everything runs as a **GitHub Actions cron** — no server needed. **No API keys for data** (yfinance + Wikipedia are free).

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
└────────────────┘                                         │
                                                           ▼
                                          ┌─────────────────────────┐
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
│   ├── fetcher.py            # yfinance + Wikipedia NASDAQ-100 list
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
│   ├── analyzer_system.md    # NASDAQ/tech equity analysis prompt
│   └── narrative_system.md   # Tech-focused narrative (Mag-7, AI, yields)
├── data/snapshots/
├── docs/                     # GitHub Pages root
│   ├── index.html
│   ├── report.html
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
# on github.com, create empty repo: <your-user>/nasdaq-research-agent
```

### 2. Migrate this code

```bash
cd nasdaq-research-agent
git init -b main
git add .
git commit -m "Initial import: NASDAQ-100 research agent"
git remote add origin https://github.com/<your-user>/nasdaq-research-agent.git
git push -u origin main
```

### 3. Enable GitHub Pages

Repository → **Settings** → **Pages** → Source: **GitHub Actions**.

### 4. Configure secrets

| Scope | Name | Required | Value |
|---|---|---|---|
| Secret | `ANTHROPIC_API_KEY` | ✅ | `sk-ant-…` |
| Secret | `TELEGRAM_BOT_TOKEN` | ✅ | from @BotFather |
| Secret | `TELEGRAM_CHAT_ID` | ✅ | your chat id |
| Variable | `DASHBOARD_URL` | ✅ | `https://<user>.github.io/nasdaq-research-agent/` |

### 5. First run

Actions → **Daily NASDAQ-100 Research** → **Run workflow**.

## Running locally

```bash
pip install -e ".[dev]"
cp .env.example .env

python -m src.main --dry-run       # fetch + rank only
python -m src.main --skip-telegram  # full run, no telegram
python -m src.main                  # full run
```

Tests + lint:

```bash
python -m pytest
python -m ruff check src tests
```

## NASDAQ-100 vs S&P 500

This agent is specifically designed for the **tech/growth-heavy** NASDAQ-100:

- **Prompts** reference Mag-7 concentration, AI capex cycles, multiple
  sensitivity to Treasury yields, options gamma dynamics
- **Universe** is ~100 stocks (vs 500 in S&P), so analysis is more focused
- **Constituent list** auto-updated from Wikipedia on every run — rebalances
  are reflected immediately
- Same GICS sector tags but heavily skewed toward Technology and
  Communication Services

## Cost

- **yfinance + Wikipedia**: Free, no API key
- **Claude Sonnet 4.6**: ~2 calls/day. Prompt caching applied.
  Expected: **~$0.03–0.08/day**

## Tuning (env vars)

| Variable | Default | Description |
|---|---|---|
| `TOP_K_GAINERS` | `5` | Number of top gainers |
| `MIN_VOLUME_USD` | `10000000` | Min daily trading value |
| `LOOKBACK_TRADING_DAYS` | `2` | Days to compare |
| `NARRATIVE_LOOKBACK_DAYS` | `7` | Reports for narrative |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Claude model |
