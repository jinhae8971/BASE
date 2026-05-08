# Module index — 한 줄 설명 색인

코드 베이스에서 어떤 파일이 어떤 역할을 하는지 빠르게 찾기 위한 색인.
새 기능 추가 시 가장 가까운 모듈에 합치되, 책임이 다르면 새 파일을 만드세요.

---

## src/agents/ — LLM 전문가 에이전트

| 파일 | 책임 |
|---|---|
| `base.py` | `BaseAgent` 추상 — `gather_context` + `parse_response` + `prompt_version` 해시 |
| `macro_agent.py` | 매크로 + 글로벌 뉴스 → regime / equity_weight |
| `sector_agent.py` | 섹터 모멘텀 + 뉴스 → sector_tilts |
| `value_agent.py` | DART/pykrx 멀티플 → value picks |
| `quant_agent.py` | regime-conditional 팩터 랭킹 → REGIME_WEIGHTS |
| `execution_agent.py` | 결정적 주문 계획 + DailyRiskGuard + 옵션 LLM 검토 |
| `reflection_agent.py` | 30일 attribution 마크다운 리포트 작성 |
| `reflection_apply.py` | 화이트리스트 patches 자동 적용 (settings.yaml 직접 수정) |

## src/orchestrator/ — 합의 + 최적화

| 파일 | 책임 |
|---|---|
| `consensus.py` | 신뢰도 가중 합의 — equity_weight / regime / sector_tilts / ticker_scores |
| `optimizer.py` | score_weighted (기본) / mean_variance / black_litterman |
| `ab_book.py` | 모든 옵티마이저 동시 실행 + 월말 우승자 자동 회전 |

## src/broker/ — KIS Open API

| 파일 | 책임 |
|---|---|
| `kis_client.py` | OAuth + REST: get_price, get_orderbook, place_order, cancel_order, get_account_state, get_unfilled_orders |
| `kis_websocket.py` | H0STCNT0 실시간 체결가 구독 → 1.5% 변동 시 stops |
| `tick_size.py` | KRX 호가단위 테이블 + snap_to_tick + daily_limit_band |
| `rate_limiter.py` | TokenBucket (5 req/s, burst 10) — 모든 KIS 호출 직전 _throttle() |

## src/data/ — 리서치 데이터

| 파일 | 책임 |
|---|---|
| `universe.py` | KOSPI200 / KRX300 / KOSDAQ150 / UNION 멤버 + 거래정지 필터 |
| `market.py` | 가격 패널 + 섹터 스냅샷 + 팩터 패널 (V/M/Q/L/S/F/breakout) + flow |
| `fundamentals.py` | pykrx 멀티플 + DART ROE/부채비율 augmentation |
| `macro.py` | yfinance + ECOS — 금리/FX/원자재/equity/credit + overnight shock |
| `news.py` | 한국 RSS + Haiku sentiment 캐시 |
| `news_global.py` | 영문 글로벌 RSS (Reuters/MarketWatch/FT 등) |

## src/portfolio/ — 리스크 + 포지션 관리

| 파일 | 책임 |
|---|---|
| `risk.py` | RiskMetrics — CAGR/Sharpe/MDD/IR/TE/α/β |
| `risk_guards.py` | DailyRiskGuard (loss_kill / mdd / turnover) + NAV 영속화 |
| `stops.py` | per-name hard stop -12% + trailing take -10% |
| `pyramid.py` | +20%/+40% 자동 추가매수 (winners scale up) |
| `position_state.py` | SQLite 테이블: entry/peak/qty/pyramid_levels |
| `hedge.py` | 방어 자산 ETF basket — 411060 / 304660 / 132030 (현물 only) |
| `divergence.py` | paper-vs-live NAV 곡선 비교 + 알림 |
| `allocator.py` | PositionSizer — vol-target / Kelly fraction (현재 미사용) |

## src/memory/ — 의사결정 저장소

| 파일 | 책임 |
|---|---|
| `journal.py` | SQLite — 모든 의사결정 + outcomes (1w/1m/3m) + prompt_version 컬럼 |
| `outcomes.py` | 매일 EOD에 forward return 백필 (idempotent) |
| `rag.py` | Chroma 우선 + TF-IDF fallback — 과거 유사 결정 검색 |

## src/learning/ — ML 자가학습

| 파일 | 책임 |
|---|---|
| `booster.py` | 3-tier 회귀: MLP (n≥50) / Ridge (5~49) / mean fallback — consensus 가중치 nudge |
| `apply.py` | booster 결과를 reflection report에 patches로 첨부 → 자동 적용 파이프라인 |
| `bandit.py` | ε-greedy TWAP interval 선택, 매일 슬리피지로 보상 업데이트 |

## src/scheduler/ — 운영 오케스트레이션

| 파일 | 책임 |
|---|---|
| `scheduler.py` | APScheduler BlockingScheduler — 매일 cron + jitter + heartbeat + Prometheus 서버 |
| `daily_pipeline.py` | research / order / monitor_unfilled / intraday_stops / eod phase 정의 |
| `state.py` | 일일 타깃 영속화 (research → order 사이) |
| `morning_report.py` | 08:30 1-pager — NAV·MDD·Top5·imminent stops |

## src/common/ — 인프라

| 파일 | 책임 |
|---|---|
| `config.py` | pydantic-settings + YAML loader + lru_cache |
| `logging.py` | structlog + 회전 파일 핸들러 (data_store/logs/mais.log) |
| `metrics.py` | Prometheus Counter/Gauge/Histogram + no-op fallback |
| `notifications.py` | Slack + Telegram + 카테고리 게이트 + digest + dashboard deeplink |
| `calendar.py` | KRX 거래일 (pykrx 우선, 평일 fallback) — 모든 phase에서 게이트 |
| `types.py` | Pydantic 모델 — AgentProposal / PortfolioTarget / Order / ExecutionResult |
| `llm.py` | Anthropic 클라이언트 + 프롬프트 캐싱 |

## src/dashboard/ — Streamlit UI

| 파일 | 책임 |
|---|---|
| `app.py` | 10페이지 단일 모듈: Overview / Today / Positions / X-ray / Learning / Alerts / Journal / Backtest / Control / Reflection |

## src/cli.py — 운영자 CLI

| 명령 | 동작 |
|---|---|
| `mais status` | NAV / MDD / 포지션 / 활성 플래그 한 페이지 |
| `mais positions` | 색상 PnL 테이블 |
| `mais today` | 오늘 타깃 + 컨텍스트 |
| `mais doctor` | 환경/패키지/거래일/settings 점검 |
| `mais logs` | tail + grep + agent 필터 |
| `mais backup` / `restore` | data_store/ tar.gz |
| `mais set <key> <value>` | settings.yaml 안전한 편집 + 자동 cache 무효화 |
| `mais flag <name> on/off` | 기능 플래그 토글 (alias 지원) |
| `mais run <phase>` | research / order / eod / intraday / monitor / reflect 실행 |
| `mais init` | 대화형 셋업 마법사 |

## src/backtest/ — 시뮬레이터

| 파일 | 책임 |
|---|---|
| `engine.py` | 이벤트 기반 백테스트 — Quant only, 슬리피지/수수료/세금, KOSPI 비교 |

---

## tests/ — 180+ 테스트

검증 영역:
- consensus / optimizer / risk metrics
- tick size / risk guards / stops / graduated MDD
- universe / news_global / hedge / bandit / booster / booster_mlp
- agent contract / state / divergence
- KIS websocket / docker setup / scheduler jitter / calendar
- notifications categories / deeplink / rate limit
- e2e_pipeline (실 LLM 호출 stub)
- dashboard pages / dashboard_ux / breakout factor

```bash
PYTHONPATH=src python -m pytest -q --ignore=tests/test_e2e_pipeline.py
# 179 passed + 1 skipped (sklearn 미설치 환경에서 MLP 테스트 skip)
```

---

## scripts/ — 후크 스크립트

| 파일 | 용도 |
|---|---|
| `run_daily.py` | 단일 phase 실행 (mais run 으로 대체 가능) |
| `run_backtest.py` | 백테스트 실행 |
| `run_reflection.py` | 즉시 reflection |
| `kill_switch.py` | 응급 청산 (--confirm I-UNDERSTAND 필수) |

---

## 의존성 그래프 (간단히)

```
cli ──┐
      ├── scheduler.daily_pipeline ──┬── agents.* ──┬── data.*
      │                              │              ├── memory.*
      │                              │              └── orchestrator.*
      │                              ├── broker.kis_client ──── tick_size, rate_limiter
      │                              ├── portfolio.* ─────────── (risk_guards, stops, pyramid)
      │                              └── learning.* ──────────── (booster, bandit, apply)
      └── (every command also reads common.config + writes via mais set/flag)

scheduler.scheduler (APScheduler) ── starts ──> common.metrics (Prometheus :9100)
                                  ── runs cron ─> daily_pipeline phases
                                  ── triggers ─> reflection + booster + ab_book

dashboard.app ── reads ───── memory.journal, portfolio.position_state, scheduler.state
              ── writes ──── settings.yaml (Control panel), data_store/excluded_tickers.json
              ── triggers ── research_phase / eod_phase / kill switch / reflection_apply
```

---

## 새 기능 추가 시 가이드

| 작업 | 어디에 |
|---|---|
| 새 LLM 에이전트 | `src/agents/<name>_agent.py` + `config/prompts/<name>.md` + scheduler에 등록 |
| 새 데이터 소스 | `src/data/<name>.py` (graceful fallback 필수) |
| 새 리스크 가드 | `src/portfolio/risk_guards.py::DailyRiskGuard.evaluate` |
| 새 옵티마이저 | `src/orchestrator/optimizer.py::PortfolioOptimizer._optimize_quant` 분기 |
| 새 ML 모델 | `src/learning/booster.py::train_agent_quality_model` 3-tier 확장 |
| 새 알림 카테고리 | `config/settings.yaml::notifications.categories` 만 추가 |
| 새 대시보드 페이지 | `src/dashboard/app.py::PAGES` dict에 함수 등록 |
| 새 CLI 명령 | `src/cli.py` Typer 데코레이터 |

새 기능은 **항상 기본 OFF + 단위테스트 + ruff clean** 원칙으로 추가하세요.
