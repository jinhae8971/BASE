# Multi-Agent Investment System (MAI-System)

> 5 전문가 AI 에이전트가 매일 협업하여 한국 주식시장에서 KOSPI 대비 초과수익을 추구하는 자가학습형 자동 투자 시스템

[![Status](https://img.shields.io/badge/status-alpha-orange)]()
[![Python](https://img.shields.io/badge/python-3.11+-blue)]()

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
 Data Layer  →  5 Specialist Agents  →  Orchestrator  →  Execution Agent  →  KIS API
                       ↓                      ↓                ↓
                    Decision Journal + Reflection Loop (자기학습)
```

### 5개 전문가 에이전트

| 에이전트 | 역할 | 출력 |
|---|---|---|
| 🌍 **MacroAgent** | 매크로·레짐 분석 | 주식/현금 비중 |
| 🏭 **SectorAgent** | 섹터 로테이션 | Over/Underweight 섹터 |
| 💎 **ValueAgent** | 가치 평가 (DCF, 멀티플) | 저평가 종목 후보 |
| 📊 **QuantAgent** | 팩터 기반 정량 랭킹 | Top-N 스코어 리스트 |
| ⚙️ **ExecutionAgent** | 주문 실행 + 리스크 가드 | KIS 주문 |

---

## 🔄 자가학습 루프

1. 모든 의사결정을 **Decision Journal**에 기록 (reasoning + context + outcome)
2. 주간/월간 **Reflection Agent**가 에이전트별 Attribution 분석
3. 과거 유사 상황을 **RAG memory**로 조회하여 현재 컨텍스트에 주입
4. 실패 케이스 클러스터링 → 프롬프트/파라미터 개선 제안 (사람 승인 후 반영)

---

## 🧩 기술 스택

- **Python 3.11**
- **Anthropic Claude API** (Opus 4.6 = 리서치, Haiku 4.5 = 실행/요약, 프롬프트 캐싱)
- **한국투자증권 Open API** (KIS)
- **pykrx / FinanceDataReader / OpenDartReader / yfinance**
- **cvxpy / PyPortfolioOpt / riskfolio-lib**
- **Chroma** (RAG), **SQLite/Postgres** (저널)
- **APScheduler**, Docker Compose

---

## 📁 프로젝트 구조

```
src/
├── agents/           # 5개 전문가 + Reflection
├── orchestrator/     # Consensus + 포트폴리오 최적화
├── broker/           # KIS API 래퍼
├── data/             # 시장/재무/매크로/뉴스 수집
├── portfolio/        # 리스크·비중 계산
├── backtest/         # 이벤트 기반 백테스트
├── memory/           # Decision Journal + RAG
├── scheduler/        # 일일 파이프라인
├── upbit/            # 업비트 알트코인 데이트레이딩 (독립 서브시스템)
└── dashboard/        # FastAPI 백엔드 + SPA 트레이딩 대시보드
config/
├── settings.yaml     # 리스크 한도·유니버스·스케줄 (+ upbit 섹션)
└── prompts/          # 에이전트 시스템 프롬프트
```

---

## 🪙 업비트 알트코인 자동매매 (Upbit Desk)

국내주식 스택과 **독립적으로** 동작하는 크립토 데이트레이딩 서브시스템.
다가올 상승장에서 알트코인 펌핑의 **베타 수익**을 노린다.

- 매일 **09:10 (KST)** 에 KRW 마켓을 스캔해 **거래량 · 수급 · 차트 · 베타** 4축 종합점수 상위 종목을 자동 매수
- 5분 간격 모니터링으로 익절 · 손절 · 트레일링 스톱 · 보유시간 · 국면 전환 청산
- **장기보유로 등록한 코인은 매수도 매도도 하지 않는다** (전량 청산·강제 청산에서도 보호)
- BTC 국면이 `risk_off` 면 신규 진입을 전면 차단
- 포트폴리오 · 거래내역 · 분석내역 · 전략 · 장기보유 · 설정 7개 탭의 트레이딩 대시보드
- **기본값은 모의(paper) 모드** — 실거래 전환은 확인 문구 입력 필요

**도커로 운영 (권장)** — 호스트에 파이썬 없이, 크래시·재부팅에도 자동 복구됩니다.

```bash
docker compose up -d --build     # http://127.0.0.1:8787
docker compose logs -f
```

**또는 파이썬 직접 실행** — 아래 한 줄이면 가상환경 생성·의존성 설치·환경 점검·
대시보드 실행·브라우저 열기까지 전부 처리합니다.

```bash
# macOS / Linux
./start-upbit.sh

# Windows — start-upbit.bat 더블클릭, 또는 PowerShell 에서
.\start-upbit.ps1
```

수동으로 하려면:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[upbit]"
mais-upbit doctor      # 의존성·설정·저장소·API·포트 점검
mais-upbit serve       # http://127.0.0.1:8787
```

API 키는 대시보드 **설정** 탭에서 입력하며 `data_store/` 아래에 암호화 저장된다.

📖 상세: [`docs/upbit_trading.md`](docs/upbit_trading.md)

---

## 🚀 빠른 시작

```bash
# 1. 환경 설정
python -m venv .venv && source .venv/bin/activate
pip install -e ".[equity,dev]"     # 국내주식 스택 (업비트만 쓸 거면 아래 6번만 해도 됩니다)

# 2. 환경변수
cp .env.example .env  # KIS_APP_KEY, KIS_APP_SECRET, ANTHROPIC_API_KEY 등 입력

# 3. 일일 파이프라인 (모의투자)
python scripts/run_daily.py --env paper

# 4. 백테스트
python scripts/run_backtest.py --start 2015-01-01 --end 2025-12-31

# 5. 리플렉션 (주간/월간)
python scripts/run_reflection.py

# 6. 업비트 자동매매 대시보드 (독립 실행 — 국내주식 스택 불필요)
pip install -e ".[upbit]"
mais-upbit doctor                       # 실행 전 환경 점검
mais-upbit serve                        # http://127.0.0.1:8787
```

> 의존성은 서브시스템별로 분리되어 있습니다. `[equity]` 는 KIS·DART·최적화·RAG 스택,
> `[upbit]` 는 크립토 대시보드만 설치합니다. 둘 다 쓰려면 `pip install -e ".[equity,upbit,dev]"`.

---

## 🗺️ 로드맵

- [x] **Phase 0**: 레포 스캐폴딩, CI, 문서
- [ ] **Phase 1**: 데이터 파이프라인 + KIS 래퍼 (모의)
- [ ] **Phase 2**: 5개 에이전트 MVP + 오케스트레이터
- [ ] **Phase 3**: 백테스트 엔진
- [ ] **Phase 4**: 자가학습 (Journal + Reflection + RAG)
- [ ] **Phase 5**: 모의투자 2개월 검증
- [ ] **Phase 6**: 대시보드 + 알림
- [ ] **Phase 7**: 실전 소액 투입

---

## ⚠️ 면책조항

- 본 프로젝트는 **개인 연구/학습 용도**입니다.
- 투자의 최종 책임은 사용자 본인에게 있으며, 본 시스템의 결정에 따른 손실에 대해 어떠한 보증도 하지 않습니다.
- 투자자문업 인가를 받지 않았으므로 타인에게 자문/권유 목적으로 사용할 수 없습니다.

---

## 📄 License

MIT
