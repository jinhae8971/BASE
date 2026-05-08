# Multi-Agent Investment System (MAI-System)

> 5 전문가 AI 에이전트가 매일 협업하여 한국 주식시장에서 KOSPI 대비 초과수익을 추구하는 자가학습형 자동 투자 시스템

[![Status](https://img.shields.io/badge/status-beta-yellow)]()
[![Python](https://img.shields.io/badge/python-3.11+-blue)]()
[![Tests](https://img.shields.io/badge/tests-179%20pass-brightgreen)]()

---

## 🚀 30초 시작

```bash
git clone <repo> mais && cd mais
docker compose build
docker compose run --rm scheduler mais init   # 대화형 셋업
docker compose up -d scheduler dashboard
# → http://localhost:8501 → "▶ 지금 첫 리서치 실행"
```

이게 전부입니다. **자세한 운영 가이드는 [`docs/HANDOVER.md`](docs/HANDOVER.md)** 를 보세요.

---

## 🎯 목표

| 지표 | 목표 |
|---|---|
| **CAGR** | KOSPI + 5%p 이상 |
| **MDD** | ≤ 15% |
| **Sharpe** | ≥ 1.0 |
| **Information Ratio** | ≥ 0.5 |

---

## 🏗️ 아키텍처

```
 Data Layer  →  5 Specialist Agents  →  Consensus + Optimizer  →  Execution Agent  →  KIS API
   pykrx          Macro · Sector              conviction-weighted            risk guards
   fundamentals    Value · Quant              + score_weighted /             tick-size snap
   macro RSS       Execution (LLM check)      mean_variance /                ADV cap
                                              black_litterman                TWAP slicing
                       ↓                              ↓                          ↓
                    Decision Journal + Reflection + RAG + ML Booster + Bandit
                                              ↓
                            대시보드 (10페이지) + Slack/Telegram + Prometheus
```

### 5개 전문가 에이전트

| 에이전트 | 역할 | 출력 |
|---|---|---|
| 🌍 **MacroAgent** | 매크로·레짐·sentiment 분석 | regime, equity_weight |
| 🏭 **SectorAgent** | 섹터 로테이션 | sector_tilts |
| 💎 **ValueAgent** | 가치 평가 (DCF, 멀티플) | 저평가 종목 picks |
| 📊 **QuantAgent** | regime-conditional 팩터 랭킹 | Top-N 스코어 picks |
| ⚙️ **ExecutionAgent** | 주문 + 가드 + (옵션) LLM 검토 | KIS 주문 |

### 6번째: ReflectionAgent — 자가학습

매주 금 18:00, 30일 attribution 분석 → 마크다운 리포트 + (옵션) 자동 적용 patches.

---

## ⭐ 핵심 특징

- **현물·롱 only** (선물·숏·레버리지 미사용)
- **강세장 모멘텀 친화** — Quant 가중치 45%, regime-conditional 팩터 (risk_on에서 momentum 0.45 + breakout 0.15)
- **"가는 말에 타오른다"** — 비대칭 손익관리 (BUY 4% drift / SELL 8% drift), +20%/+40%에서 자동 추가매수 (pyramid)
- **장중 stops** — 30분마다 / 옵션으로 KIS 실시간 웹소켓
- **그라데이션 MDD** — 10/12/15% 도달 시 25/50/75% 자동 trim
- **자가학습 폐쇄 루프** — Reflection + ML Booster + A/B optimizer paper books + ε-greedy TWAP bandit
- **운영자 친화 대시보드** — 10페이지에서 모든 모니터링/제어 (Kill switch 포함)
- **모든 가드 declared = enforced** — daily_loss_kill / max_portfolio_mdd / max_turnover_daily / hard_stop / trailing_take

---

## 🧩 기술 스택

- **Python 3.11**, type hints, ruff + mypy + pre-commit
- **Anthropic Claude API** — Opus 4.6 (research), Haiku 4.5 (sentiment), 프롬프트 캐싱
- **한국투자증권 Open API** — REST + 실시간 웹소켓
- **pykrx / FinanceDataReader / OpenDartReader / yfinance / feedparser**
- **PyPortfolioOpt / cvxpy** — Black-Litterman, mean-variance
- **Chroma + sentence-transformers** — RAG memory
- **scikit-learn** — Ridge / MLP booster
- **APScheduler + pytz** — 한국시간 cron
- **Streamlit** — 운영 대시보드
- **Prometheus client** — 메트릭
- **Docker / docker-compose** — Windows·Linux 일관 운영

---

## 📁 프로젝트 구조

```
src/
├── cli.py             # mais 통합 CLI
├── agents/            # 5 specialists + reflection + reflection_apply
├── orchestrator/      # consensus + optimizer + ab_book
├── broker/            # KIS REST + 웹소켓 + tick size + rate limiter
├── data/              # market / fundamentals / macro / news / news_global / universe
├── portfolio/         # risk_guards / stops / pyramid / position_state / hedge / divergence
├── memory/            # journal + RAG + outcome backfill
├── learning/          # booster (ridge/MLP) + bandit + apply
├── scheduler/         # daily_pipeline + state + morning_report
├── common/            # config / logging / metrics / notifications / calendar
└── dashboard/         # Streamlit 10-page app
config/
├── settings.yaml      # 모든 운영 파라미터 (단일 source of truth)
└── prompts/           # 6개 에이전트 시스템 프롬프트
docs/
├── HANDOVER.md        # ★ 종합 인수인계 문서
├── architecture.md, agents.md, risk_policy.md, docker.md
├── improvements.md    # 26개 개선 항목 트래커 (모두 closed)
└── MODULES.md         # src/ 모듈 한 줄 색인
```

---

## 🗺️ 로드맵 (모두 완료)

- [x] **Phase 0**: 스캐폴딩 + CI
- [x] **Phase 1**: 실데이터 파이프라인 + KIS 래퍼
- [x] **Phase 2**: 5 에이전트 + Black-Litterman 옵티마이저
- [x] **Phase 3**: 이벤트 백테스트 엔진
- [x] **Phase 4**: Decision Journal + outcome 백필 + Chroma RAG
- [ ] **Phase 5**: 모의투자 2개월 검증 *(사용자 영역)*
- [x] **Phase 6**: Streamlit 대시보드 (10페이지)
- [ ] **Phase 7**: 실전 소액 투입 *(사용자 영역)*
- [x] **Trader hardening**: 호가 스냅, 가드, 알림, 메트릭 (×26 개선 항목)
- [x] **Self-learning loop**: Reflection + ML Booster + A/B + Bandit
- [x] **UX overhaul**: mais CLI + 한국어 대시보드 + onboarding

---

## ⚠️ 면책조항

- 본 프로젝트는 **개인 연구/학습 용도**입니다.
- 투자의 최종 책임은 사용자 본인에게 있으며, 본 시스템의 결정에 따른 손실에 대해 어떠한 보증도 하지 않습니다.
- 투자자문업 인가를 받지 않았으므로 타인에게 자문/권유 목적으로 사용할 수 없습니다.

---

## 📄 License

MIT
