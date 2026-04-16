# Dow Jones 30 Research Agent

Daily Dow Jones Industrial Average (DJIA) top-gainer research agent. Runs **every US trading day after market close** and:

1. Scrapes the **Dow 30 constituent list** from Wikipedia + downloads OHLCV via **yfinance** (no API key)
2. Persists daily snapshots for exact **2-trading-day price change**
3. Picks the **top 5 gainers** by 2-day return
4. Fetches news via **yfinance built-in news feed**
5. Uses **Claude Sonnet 4.6** (with prompt caching) to analyze each gainer — catalysts, GICS sector, blue-chip context, confidence score
6. Synthesizes a **7-day market narrative** (sector rotation, Dow-specific dynamics)
7. Deploys a **GitHub Pages** dashboard with current + historical reports
8. Sends a **Telegram** summary with deep-link

**No server, no data API keys** — GitHub Actions cron + yfinance + Wikipedia.

---

## Dow-specific design

| Aspect | Detail |
|---|---|
| **Universe** | Only 30 stocks — the most focused of all agents |
| **Weighting** | Dow is **price-weighted** (not market-cap). Prompts note when high-priced stocks (UNH, GS, MSFT) have outsized index impact |
| **Character** | Blue-chip / dividend / industrial focus. Analysis covers: earnings, macro data (ISM, CPI), Fed, dividend changes, buybacks, credit ratings |
| **Risks** | Margin pressure, FX headwinds, regulatory, cyclical downturn, pension obligations, commodity input costs |
| **Volume filter** | $5M minimum (lower than S&P 500 / NASDAQ since all Dow stocks are highly liquid) |

## Setup (one-time)

### 1. Create repo + migrate

```bash
cd dow30-research-agent
git init -b main
git add .
git commit -m "Initial import: Dow 30 research agent"
git remote add origin https://github.com/<your-user>/dow30-research-agent.git
git push -u origin main
```

### 2. Enable GitHub Pages

Settings → Pages → Source: **GitHub Actions**

### 3. Secrets

| Scope | Name | Required |
|---|---|---|
| Secret | `ANTHROPIC_API_KEY` | ✅ |
| Secret | `TELEGRAM_BOT_TOKEN` | ✅ |
| Secret | `TELEGRAM_CHAT_ID` | ✅ |
| Variable | `DASHBOARD_URL` | ✅ `https://<user>.github.io/dow30-research-agent/` |

### 4. First run

Actions → **Daily Dow 30 Research** → **Run workflow**

## Running locally

```bash
pip install -e ".[dev]"
cp .env.example .env

python -m src.main --dry-run        # fetch + rank only
python -m src.main --skip-telegram   # full run, no telegram
python -m src.main                   # full run

python -m pytest                     # tests
python -m ruff check src tests       # lint
```

## Cron

`0 22 * * 0-4` UTC (Sun–Thu) = Mon–Fri ~17:00 ET (after market close)

## Cost

- **Data**: Free (yfinance + Wikipedia)
- **Claude**: ~$0.03–0.08/day (2 calls, prompt caching)

## Tuning

| Variable | Default | Description |
|---|---|---|
| `TOP_K_GAINERS` | `5` | Top gainers count |
| `MIN_VOLUME_USD` | `5000000` | Min daily trading value |
| `LOOKBACK_TRADING_DAYS` | `2` | Days to compare |
| `NARRATIVE_LOOKBACK_DAYS` | `7` | Reports for narrative |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Claude model |
