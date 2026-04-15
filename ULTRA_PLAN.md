# Ultra Plan — Crypto Multi-Agent Auto-Trading System

> Living document. Authoritative design reference for `crypto-agent/`.

## 0. Philosophy

| Principle | Meaning |
|---|---|
| Alpha over BTC | Primary success metric is outperforming BTC HODL on rolling 3-month basis. |
| MDD first | MDD < 15% target, 25% hard cap → circuit breaker. |
| Explainable | Every trade stored with full agent rationale for post-mortem learning. |
| Self-improving | Post-trade reflection updates agent ELO weights automatically. |
| Fail-safe | Dry-run default. Live mode requires explicit flag + non-withdraw API key. |

## 1. Six Agents

1. **Crypto Research** (Sonnet 4.6) — CryptoPanic, CoinGecko, GitHub activity
2. **Macro** (Sonnet 4.6) — FRED (DXY, 10Y, CPI, FFR), Yahoo Finance (SPX, VIX)
3. **Sector** (Sonnet 4.6) — CoinGecko categories, DefiLlama TVL
4. **Value Investor** (Opus 4.6) — Tokenomics, FDV/MCAP, unlock schedule
5. **Crypto Quant** (Sonnet 4.6) — OHLCV indicators, volatility regime
6. **Executor** (Opus 4.6) — Portfolio construction, Binance orders

Plus **Reflection** (Opus 4.6) for post-mortem learning.

All LLM calls use Anthropic prompt caching on system prompt + universe definition.

## 2. Data Layer (free tier only)

| Source | Use | Rate limit strategy |
|---|---|---|
| Binance public REST/WS | OHLCV, orderbook, account | WS for live, REST cached 60s |
| CoinGecko free | Market cap, categories | 30 req/min, Redis cache 5 min |
| DefiLlama | TVL by chain/protocol | unlimited, cache 10 min |
| CryptoPanic free | News + sentiment labels | 500/day budget |
| FRED | Macro series | daily refresh |
| Yahoo Finance (yfinance) | SPX, VIX, DXY, Gold | daily refresh |

All raw pulls archived to Parquet for deterministic backtests.

## 3. Portfolio & Risk

### Universe
- Core: BTC, ETH (≥ 40% combined)
- Satellite: Binance top 50 by market cap, 24h volume > $50M, listed > 30d
- Excluded: leveraged tokens, stablecoins (except USDT cash leg), new listings

### Construction
1. Aggregate agent signals: `score_i = Σ w_agent × signal_agent_i`, with `w_agent` from ELO
2. Risk Parity base allocation, capped by ½-Kelly
3. No shorts (spot only, Phase 1)
4. Cash (USDT) weight = `f(macro_regime)`, up to 60% in risk-off

### Guardrails
| Layer | Rule | Action |
|---|---|---|
| Position | Unrealized loss > 8% | Auto stop-loss |
| Daily | Daily P&L < -3% | Freeze new buys today |
| Weekly | Weekly P&L < -7% | Halve all positions |
| **MDD** | **Equity DD > 15%** | **Flatten to USDT + human ack** |
| API | Binance latency > 2s or 5xx | Skip order, alert |
| Sanity | Single order > 30% of equity | Block |

## 4. Learning Loop

### Trade Journal (Postgres)
- Every decision: agent inputs, agent outputs, optimizer snapshot, final orders, realized P&L, holding period, attribution.
- Every reflection: per-agent prediction vs. outcome, natural-language `lesson`.

### Mechanisms
1. **Reflection** — on each position close, Reflection agent compares predicted vs realized, stores lesson in Qdrant (pgvector). Next run's agents retrieve similar historical lessons via RAG.
2. **Agent ELO** — each agent's signals shadow-traded into an independent virtual portfolio. Monthly Sharpe contribution updates ELO, which feeds back into aggregator weights.
3. **Backtest Regression** — weekly walk-forward replay of last 30 days vs alternative strategies (BTC HODL, equal-weight top 10). Optuna tunes α, Kelly fraction, lookbacks. Changes ship only after 2-week shadow A/B.

## 5. Roadmap

| Phase | Goal | Exit Criteria |
|---|---|---|
| P0 Scaffold | Repo, CI, docker-compose, agent stubs | `pytest` green, dry run end-to-end |
| P1 Data Layer | Binance / CoinGecko / DefiLlama / CryptoPanic / FRED clients with cache + archive | 7 days of archived parquet |
| P2 Agents | Real LLM calls, JSON schema enforcement | Sample inputs → valid outputs |
| P3 Orchestrator | Daily DAG, aggregator, optimizer | End-to-end dry run w/ real data |
| P4 Backtest | 2023–2025 replay engine | Alpha vs BTC measured |
| P5 Paper trading | Binance testnet, 2 weeks | MDD < 10%, order success > 99% |
| P6 Learning loop | ELO + reflection + Optuna | Shadow Sharpe improves |
| P7 Live (1,000 USDT) | Real capital, small | 4 weeks w/ zero guardrail breaches |
| P8 Scale | Capital increase, strategy versioning | Quarterly Alpha vs BTC |

**Hard rule**: No live trading before P5 passes.

## 6. KPIs

| Metric | Target | Hard Limit |
|---|---|---|
| Annualized Return | > BTC + 10pp | — |
| Alpha vs BTC (rolling 3m) | > 0 | 3m neg streak → freeze |
| Sharpe | > 1.5 | > 1.0 |
| Sortino | > 2.0 | — |
| MDD | < 15% | < 25% (circuit) |
| Win Rate | > 50% | — |
| Profit Factor | > 1.8 | > 1.3 |
| Monthly Turnover | < 300% | — |

## 7. Security

- API keys: Spot trade only. **Withdraw OFF, Futures OFF**.
- Binance IP whitelist.
- Secrets via `.env` (dev) / 1Password CLI (prod). Never committed.
- `HALT` file kill switch.
- `TRADING_MODE ∈ {dry, paper, live}` with explicit promotion only.

## 8. Open Items (for next milestones)

- [ ] Reflection prompt template design
- [ ] ELO initial ratings and update formula
- [ ] Korean tax reporting JSON schema
- [ ] Optional fallback exchange via CCXT (OKX/Bybit)
