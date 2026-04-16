# Crypto Research Agent

A personal crypto research agent that runs **daily at 07:00 KST** and:

1. Fetches the top 500 coins by market cap from **CoinGecko**
2. Computes exact **48-hour price change** from a persisted daily snapshot
3. Picks the **top 5 gainers** (excluding stablecoins, wrapped / LST derivatives, and low-liquidity pumps)
4. Uses **Claude Sonnet 4.6** (with prompt caching) to analyze *why* each coin pumped, pulling in recent **CryptoPanic** news headlines as context
5. Synthesizes a **market-narrative read** over the last 7 days' reports (hot / cooling sectors, week-over-week rotation, actionable PM insight)
6. Writes a JSON report to `docs/reports/YYYY-MM-DD.json` and updates `docs/reports/index.json`
7. Deploys the `docs/` folder as a **GitHub Pages** dashboard showing today's + historical reports
8. Sends a **Telegram MarkdownV2** summary with a deep-link into the dashboard for the day

Everything runs as a **GitHub Actions cron job** — no server, no manual steps.

---

## Architecture

```
GitHub Actions (cron: 22:00 UTC = 07:00 KST)
        │
        ▼
┌────────────┐   ┌──────────┐   ┌──────────────────┐
│ Fetcher    │──▶│ Ranker   │──▶│ Analyzer (Claude)│──┐
│ CoinGecko  │   │ 48h Top5 │   │  + CryptoPanic    │  │
└────────────┘   └──────────┘   └──────────────────┘  │
        │                                              ▼
        ▼                                   ┌──────────────────┐
┌────────────┐                               │ Narrative (Claude)│
│ Snapshots  │◀── loaded by ranker ──────────│  7-day synthesis  │
│ (2 days ago)│                              └─────────┬────────┘
└────────────┘                                         │
                                                       ▼
                                        ┌─────────────────────────┐
                                        │ docs/reports/*.json     │ ─▶ GitHub Pages
                                        └─────────────────────────┘
                                                       │
                                                       ▼
                                               ┌──────────────┐
                                               │ Telegram Bot │
                                               │ + deep link  │
                                               └──────────────┘
```

## Repository layout

```
.
├── src/                      # Python package
│   ├── main.py               # daily pipeline entry point
│   ├── fetcher.py            # CoinGecko client
│   ├── ranker.py             # 48h top-K selection (with snapshot diff)
│   ├── news.py               # CryptoPanic client
│   ├── analyzer.py           # Claude per-coin analysis (prompt caching)
│   ├── narrative.py          # Weekly narrative synthesis
│   ├── notifier.py           # Telegram MarkdownV2 sender
│   ├── storage.py            # snapshot + report persistence
│   ├── config.py             # env-backed Settings
│   ├── models.py             # pydantic schemas
│   └── logging_setup.py
├── prompts/
│   ├── analyzer_system.md
│   └── narrative_system.md
├── data/snapshots/           # committed daily snapshots (price, mcap, vol)
├── docs/                     # ── GitHub Pages root ──
│   ├── index.html            # dashboard
│   ├── report.html           # per-date report view
│   ├── assets/{app.js, style.css}
│   └── reports/              # JSON reports + index.json
├── tests/                    # pytest unit tests
├── .github/workflows/daily.yml
├── .env.example
└── pyproject.toml
```

## Setup (one-time)

### 1. Create a new GitHub repository

```bash
# on github.com, create empty repo: <your-user>/crypto-research-agent
```

### 2. Migrate this code into it

This project currently lives as a subdirectory of the scaffold repo. To move it
into its own standalone repo:

```bash
cd crypto-research-agent
git init -b main
git add .
git commit -m "Initial import: crypto research agent"
git remote add origin https://github.com/<your-user>/crypto-research-agent.git
git push -u origin main
```

### 3. Enable GitHub Pages

Repository → **Settings** → **Pages** → Source: **GitHub Actions**.

The `daily.yml` workflow deploys `docs/` automatically on every run.

### 4. Configure secrets

Repository → **Settings** → **Secrets and variables** → **Actions**.

| Scope | Name | Required | Value |
|---|---|---|---|
| Secret | `ANTHROPIC_API_KEY` | ✅ | `sk-ant-…` |
| Secret | `TELEGRAM_BOT_TOKEN` | ✅ | from @BotFather |
| Secret | `TELEGRAM_CHAT_ID` | ✅ | your chat id (see below) |
| Secret | `CRYPTOPANIC_KEY` | optional | free key from cryptopanic.com |
| Secret | `COINGECKO_API_KEY` | optional | CoinGecko demo key |
| Variable | `DASHBOARD_URL` | ✅ | `https://<your-user>.github.io/crypto-research-agent/` |

**Getting the Telegram chat ID**:
1. Create a bot via [@BotFather](https://t.me/BotFather), save the token
2. Send any message to your bot
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and find the `chat.id`

### 5. Trigger the first run

Repository → **Actions** → **Daily Crypto Research** → **Run workflow**.

The first run has no prior snapshot, so the ranker will fall back to **24h**
change for that one day. From day 3 onward, exact 48h change is used.

## Running locally

```bash
pip install -e ".[dev]"
cp .env.example .env     # fill secrets

# dry run: fetch + rank only (no LLM, no telegram)
python -m src.main --dry-run

# full run, but don't send telegram
python -m src.main --skip-telegram

# full run
python -m src.main
```

Tests + lint:

```bash
python -m pytest
python -m ruff check src tests
```

Dashboard preview locally:

```bash
python -m http.server --directory docs 8000
# open http://localhost:8000
```

## Cron schedule

GitHub Actions uses UTC. **07:00 KST (UTC+9) = 22:00 UTC (previous day)** →
`0 22 * * *`. GitHub's scheduled workflows can be delayed by a few minutes
during high-load periods; this is expected.

## Cost notes

- **CoinGecko** free tier: 2 requests for top-500 + ~5 detail calls per run =
  well under the 30 req/min limit.
- **CryptoPanic** free tier: 5 calls per run.
- **Claude Sonnet 4.6**: ~2 calls per run (1 analyzer for all 5 coins, 1
  narrative). The system prompt is marked `cache_control: ephemeral` so
  day-2+ runs hit the prompt cache and only pay for user-message + output
  tokens. Expected cost: **~$0.03–0.08 per run**.

## Extending

- **Change the coin universe size**: `TOP_N_COINS` env (default 500)
- **Change top-K**: `TOP_K_GAINERS` env (default 5)
- **Change liquidity filter**: `MIN_VOLUME_USD` env (default $1M)
- **Tune narrative window**: `NARRATIVE_LOOKBACK_DAYS` env (default 7)
- **Add a new data source**: add a module under `src/` returning a plain dict
  and wire it into `analyzer._build_coin_context`
- **Customize prompts**: edit `prompts/analyzer_system.md` or
  `prompts/narrative_system.md` — they're hot-reloaded on each run

## Safety

- No live trading, no order placement — this is pure research.
- All data (snapshots + reports) is committed back to the repo, so history is
  fully auditable.
- Secrets never touch source files; only `.env` (gitignored) and GitHub
  Actions secrets.
