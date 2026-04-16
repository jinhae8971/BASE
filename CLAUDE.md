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

---

## Multi-Market Research Agent System (이 레포의 서브 프로젝트)

MAIS와 별도로, 이 레포에는 6개의 독립 리서치 에이전트가 서브디렉토리로 존재합니다.
각각 **독립된 GitHub 레포로 이관 완료** (jinhae8971/ 계정).

### 6개 프로젝트 구조

```
crypto-research-agent/      → jinhae8971/crypto-research-agent   ✅ pushed
kospi-research-agent/       → jinhae8971/kospi-research-agent    ✅ pushed
sp500-research-agent/       → jinhae8971/sp500-research-agent    ✅ pushed
nasdaq-research-agent/      → jinhae8971/nasdaq-research-agent   ✅ pushed
dow30-research-agent/       → jinhae8971/dow30-research-agent    ✅ pushed
global-market-orchestrator/ → jinhae8971/global-market-orchestrator ✅ pushed
```

### 각 에이전트 공통 아키텍처

```
src/
├── main.py          # 파이프라인 엔트리
├── fetcher.py       # 시장 데이터 수집
├── ranker.py        # N일 상승률 Top-K 선별
├── news.py          # 뉴스 수집
├── analyzer.py      # Claude Sonnet 4.6 분석 (프롬프트 캐싱)
├── narrative.py     # 7일 내러티브 종합
├── notifier.py      # Telegram MarkdownV2
├── storage.py       # 스냅샷 + 보고서 JSON
├── config.py        # pydantic-settings
├── models.py        # Pydantic 스키마
└── logging_setup.py
prompts/             # analyzer + narrative 시스템 프롬프트
docs/                # GitHub Pages 대시보드 (vanilla HTML/JS)
.github/workflows/   # daily cron + Pages deploy
```

### 데이터 소스 매핑

| 에이전트 | 데이터 | 뉴스 | API 키 필요 |
|---|---|---|---|
| crypto | CoinGecko | CryptoPanic | CoinGecko demo 키 권장 |
| kospi | pykrx (KRX) | 네이버 금융 | 없음 |
| sp500 | yfinance + Wikipedia | yfinance 내장 | 없음 |
| nasdaq | yfinance + Wikipedia | yfinance 내장 | 없음 |
| dow30 | yfinance + Wikipedia | yfinance 내장 | 없음 |
| orchestrator | 5개 에이전트 Pages fetch | — | 없음 |

### 이관 현황 및 남은 작업

**완료:**
- [x] 6개 GitHub 레포 생성 + 코드 push
- [x] GitHub Pages 활성화 (GitHub Actions source)
- [x] Actions Variables 등록 (DASHBOARD_URL + 오케스트레이터의 agent URLs)
- [x] crypto-research-agent Secrets 등록 (ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

**남은 작업:**
- [ ] **Secrets 수동 등록** — 5개 레포에 동일한 3개 시크릿 등록
      대상: kospi, sp500, nasdaq, dow30, global-market-orchestrator
      경로: 각 레포 → Settings → Secrets and variables → Actions → New repository secret
- [ ] **CoinGecko 403 수정** — crypto-research-agent의 CoinGecko API가 GitHub Actions IP에서 차단됨
      수정: src/fetcher.py에서 Demo API 엔드포인트 사용 또는 COINGECKO_API_KEY 시크릿 추가
      CoinGecko Demo 키: https://www.coingecko.com/en/api/pricing (무료 플랜에서 발급)
- [ ] **5개 에이전트 첫 실행** — Secrets 등록 후 각 레포 Actions → Run workflow
- [ ] **오케스트레이터 첫 실행** — 에이전트 최소 1개 성공 후 트리거
- [ ] **PAT 토큰 revoke** — 이 세션에서 사용한 ghp_ 토큰 폐기 필요

### Cron 스케줄

```
22:00 UTC (07:00 KST) — 5개 에이전트 병렬 실행
  crypto:  매일          (0 22 * * *)
  kospi:   평일          (0 22 * * 0-4)
  sp500:   평일          (0 22 * * 0-4)
  nasdaq:  평일          (0 22 * * 0-4)
  dow30:   평일          (0 22 * * 0-4)
23:30 UTC (08:30 KST) — 오케스트레이터 (매일, 30 23 * * *)
```

### 예상 비용

총 12 Claude 호출/일 ≈ $0.20–0.50/day ≈ $6–15/month
