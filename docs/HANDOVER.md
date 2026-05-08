# MAI-System 인수인계 문서

> 다른 환경에서 이어서 운영하려는 사람을 위한 단일 진입점.
> 처음부터 끝까지 이 문서 하나만 따라가면 됩니다.

**Last updated**: chunk 19 (UX-7) 까지 완료, 커밋 `10e1582` 기준.

---

## 0. 30초 요약

```bash
git clone <repo> mais && cd mais
docker compose build
docker compose run --rm scheduler mais init   # 대화형 셋업
docker compose up -d scheduler dashboard
# → http://localhost:8501 접속, "▶ 지금 첫 리서치 실행" 클릭
```

이게 전부입니다. 나머지는 운영 중에 천천히 읽으세요.

---

## 1. 시스템이 하는 일

**한국 KOSPI200 / KRX300 / KOSDAQ150 종목**을 대상으로 하는 **현물·롱-온리·자동매매 시스템**:

- **5개 LLM 전문가 에이전트**(Macro · Sector · Value · Quant · Execution)가 매일 협업
- **강세장 모멘텀 친화** 설계 — Quant 가중치 45%, regime-conditional 팩터 가중치
- **"가는 말에 타오른다"** — 비대칭 손익관리 (BUY 4% drift / SELL 8% drift), 진입 +20%/+40%에서 자동 추가매수 (pyramid)
- **장중 stops** — 30분마다 / 옵션으로 KIS 실시간 웹소켓 (1.5% 즉시 반응)
- **자가학습** — 매주 reflection + ML alpha booster + A/B optimizer paper books
- **운영자 친화** — 한국어 Streamlit 대시보드 10페이지에서 모든 모니터링/제어
- **선물·숏·레버리지 미사용** (사용자 명시적 제약)

**목표 지표** (`docs/risk_policy.md`):
- CAGR > KOSPI + 5%p
- MDD ≤ 15%
- Sharpe ≥ 1.0
- IR ≥ 0.5

---

## 2. 새 환경에서 처음부터 구축하기

### 2.1 사전 요구사항

| 도구 | 버전 | 비고 |
|---|---|---|
| Docker Desktop | 최신 | Windows 11이면 WSL2 백엔드 권장 |
| Git | 최신 | |
| 한국투자증권 (KIS) 계정 | 모의투자 활성화 | https://apiportal.koreainvestment.com/ |
| Anthropic API Key | Claude 4 사용 가능 | https://console.anthropic.com/ |
| (선택) DART API Key | | https://opendart.fss.or.kr/ |
| (선택) ECOS API Key | | https://ecos.bok.or.kr/ |
| (선택) Slack Webhook | | 알림용 |

### 2.2 코드 가져오기

```bash
git clone <repository_url> mais
cd mais

# 본 인수인계 시점 기준 안정 브랜치
git checkout claude/kis-autotrade-completion-aXI7f
```

### 2.3 환경 변수 설정 — `mais init` 마법사 (권장)

```bash
docker compose build
docker compose run --rm scheduler mais init
```

대화형으로 다음을 묻습니다:
- ANTHROPIC_API_KEY
- KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO
- KIS_ENV (paper | live) → **반드시 `paper`로 시작**
- DART_API_KEY (선택)
- SLACK_WEBHOOK_URL (선택)

`.env` 파일이 생성되고 `mais doctor`가 자동 실행되어 점검 결과를 보여줍니다.

### 2.4 수동 설정 (마법사 대신)

```bash
cp .env.example .env
# 에디터로 .env 편집
docker compose run --rm scheduler mais doctor
```

`mais doctor` 출력에서 **모든 체크가 OK**여야 다음 단계로.

### 2.5 데이터 디렉토리 + 토큰 캐시

```bash
mkdir -p data_store/logs data_store/state data_store/reflections
touch .kis_token.json   # KIS OAuth 토큰 캐시 — Docker volume 마운트용
```

### 2.6 첫 기동 + 첫 리서치

```bash
docker compose up -d scheduler dashboard
```

브라우저에서 `http://localhost:8501` 접속:
1. 첫 화면 ("아직 저널 기록이 없습니다") → **▶ 지금 첫 리서치 실행** 클릭
2. 60~120초 후 완료 → "Today" 페이지로 이동
3. 오늘 타깃 + regime + 임박 stops 확인

---

## 3. 디렉토리 구조

```
mais/
├── README.md
├── CHANGELOG.md            ← 모든 청크별 변경 요약
├── pyproject.toml          ← Python 패키지 정의 + console_scripts
├── Dockerfile              ← scheduler / dashboard / oneshot 공용
├── docker-compose.yml      ← 서비스 정의
├── .env.example            ← 환경변수 템플릿
├── .pre-commit-config.yaml ← ruff + mypy + 안전성 hook
├── .github/workflows/ci.yml
│
├── config/
│   ├── settings.yaml       ← 모든 운영 파라미터 (단일 source of truth)
│   └── prompts/            ← 6개 에이전트 시스템 프롬프트
│       ├── macro.md
│       ├── sector.md
│       ├── value.md
│       ├── quant.md
│       ├── execution.md
│       └── reflection.md
│
├── docs/
│   ├── HANDOVER.md         ← (이 문서)
│   ├── architecture.md     ← 데이터 흐름 다이어그램
│   ├── agents.md           ← 에이전트 계약
│   ├── risk_policy.md      ← 리스크 한도 + 비상 절차
│   ├── docker.md           ← Windows Docker 운영 가이드
│   ├── improvements.md     ← 개선 트래커 (모두 closed)
│   └── MODULES.md          ← 모든 src/ 파일 한 줄 설명
│
├── scripts/
│   ├── run_daily.py        ← 단일 phase 실행 (mais run 으로 대체 권장)
│   ├── run_backtest.py
│   ├── run_reflection.py
│   └── kill_switch.py      ← 응급 청산
│
├── src/
│   ├── cli.py              ← mais 명령 (status/doctor/run/flag/...)
│   ├── agents/             ← 5 specialists + reflection + reflection_apply
│   ├── orchestrator/       ← consensus + optimizer + ab_book
│   ├── broker/             ← KIS REST + 웹소켓 + tick size + rate limiter
│   ├── data/               ← market / fundamentals / macro / news / news_global / universe
│   ├── portfolio/          ← risk metrics / risk_guards / stops / pyramid /
│   │                          position_state / hedge / divergence / allocator
│   ├── memory/             ← journal (SQLite) / RAG (Chroma) / outcomes
│   ├── learning/           ← booster (ridge/MLP) + apply + bandit
│   ├── scheduler/          ← daily_pipeline + scheduler + state + morning_report
│   ├── common/             ← config / logging / metrics / notifications / calendar / types / llm
│   └── dashboard/          ← Streamlit app.py (10페이지)
│
├── tests/                  ← 180+ tests (pytest)
└── data_store/             ← 런타임 상태 (gitignored)
    ├── journal.sqlite      ← decision journal
    ├── chroma/             ← RAG embeddings
    ├── state/              ← daily targets
    ├── reflections/        ← weekly reports
    ├── ab_books/           ← A/B optimizer NAVs
    ├── nav_history.csv
    ├── paper_nav_history.csv  (선택)
    ├── bandit_twap.json
    ├── excluded_tickers.json  (선택)
    └── logs/mais.log
```

---

## 4. 일일 운영 사이클 (KST)

```
08:00  heartbeat        ← Slack/Telegram "alive" 핑
08:00  research         ← 4 LLM 에이전트 + consensus + optimizer + 타깃 저장
08:30  morning report   ← Slack/Telegram 1-pager
09:05 ±60s  order       ← KIS 주문 (overnight shock 게이트, TWAP, ADV 캡, stops)
09:35  monitor_unfilled ← 미체결 주문 cancel + bid/ask 갱신 재주문
10:00 ~ 15:00 (30분)  intraday stops  ← stops only, 추가매수 X
16:00  EOD              ← NAV 영속화, outcome 백필, A/B 정산, reflection auto-apply
금요일 18:00  reflection ← 30일 attribution + alpha/IR/TE 분석 → 마크다운 + 자동 적용 patches
월말   A/B optimizer 우승자 평가 (옵션: 자동 회전)
```

설정 (`config/settings.yaml::scheduler`):
```yaml
research_time: "08:00"
morning_report_time: "08:30"
order_time: "09:05"
monitor_unfilled_time: "09:35"
eod_review_time: "16:00"
reflection_day: "friday"
reflection_time: "18:00"
heartbeat_time: "08:00"
jitter_seconds: 60
intraday_stops_enabled: true
intraday_stops_start: "10:00"
intraday_stops_end: "15:00"
intraday_stops_interval_min: 30
```

---

## 5. 운영 인터페이스

### 5.1 대시보드 (브라우저, 권장)

`http://localhost:8501`

| 페이지 | 용도 |
|---|---|
| 📈 Overview | NAV 곡선, 활성 플래그 뱃지, 30일 의사결정 추이 |
| ☀️ Today | 오늘 타깃 Top25, regime, 임박 stops, 모닝리포트 즉시발송 |
| 💼 Positions | PnL 색상 테이블, peak/trail/pyramid, CSV 내려받기 |
| 🔬 X-ray | 팩터·섹터·포트폴리오 노출 |
| 🤖 Learning | TWAP bandit / Booster 점수 / A/B paper book |
| 🚨 Alerts | 7일 가드·stop·replace·param 이벤트 |
| 🧾 Journal | Agent/Action/티커 필터 + outcome attribution + CSV |
| ⏪ Backtest | Quant-only 백테스트 실행 |
| 🎛️ Control | **모든 플래그 토글 + 슬라이더 + 블랙리스트 + Kill switch** |
| 🪞 Reflection | 주간 리포트 읽고 patches 승인/거부 |

### 5.2 CLI (`mais` 명령)

```bash
mais status            # NAV/MDD/포지션/플래그 한 페이지
mais positions         # 색상 PnL 테이블
mais today             # 오늘 타깃 + 컨텍스트
mais doctor            # 환경/패키지/거래일/settings 점검
mais logs --tail 100 --grep error --agent quant
mais backup            # data_store/ tar.gz
mais restore <file> --confirm YES
mais set risk.hard_stop_pct 0.10
mais flag hedge on     # alias 또는 dotted path
mais run research      # phase 실행
mais run order --live  # 실거래 주문
mais init              # 대화형 셋업
```

### 5.3 Slack/Telegram 알림

운영 중 자동 발송되는 카테고리:
- **order**: 주문 제출/거부 (info/warning/error)
- **risk**: 가드 발동 (daily_loss_kill / mdd / turnover / overnight_shock)
- **agent**: LLM 에이전트 실패
- **booster**: ML 자동 적용

`settings.yaml::notifications.dashboard_url`에 대시보드 URL을 넣으면 알림에 **"대시보드 열기"** 링크가 자동 첨부됩니다.

---

## 6. 단계적 활성화 가이드

**모든 고급 기능은 기본 OFF**입니다. 다음 순서로 켜시는 것을 권장합니다 (각 ≥ 2주 안정화 후 다음 단계):

| 단계 | 기간 | 설정 | 검증 |
|---|---|---|---|
| 1 | 1~2주 | 기본 운영 (KIS_ENV=paper) | 매일 morning report 도착, journal 쌓임 |
| 2 | 2주 | `hedge.enabled: true` | risk_off regime에서 cash가 ETF로 자동 분배 확인 |
| 3 | 2주 | `execution.twap_enabled: true` | TWAP 4분할 체결 확인 |
| 4 | 2주 | `execution.twap_bandit_enabled: true` | Learning 페이지에서 bandit arm 학습 |
| 5 | 1주 | `optimizer.ab_auto_rotate: true` | 월말 A/B 우승자 자동 반영 |
| 6 | 1주 | `learning.auto_apply: true` | reflection 패치 자동 적용 |
| 7 | 2주 | `KIS_ENV=live` (소액 100만원) | 실거래 시작 |
| 8 | 1주 | `websocket.enabled: true` | 실시간 stops |

**대시보드의 🎛️ Control 페이지 토글로 한 번에 변경 가능**.

---

## 7. settings.yaml 핵심 옵션 (자주 만지는 것만)

### 리스크 (보수적으로 시작 → 운영 중 미세조정)
```yaml
risk:
  max_position_weight: 0.10      # 단일 종목 10%
  max_sector_weight: 0.30
  max_portfolio_mdd: 0.15        # 15% 도달 시 graduated trim
  daily_loss_kill: 0.03          # 일일 -3% 감지 시 신규주문 차단
  hard_stop_pct: 0.12            # 진입가 대비 -12% 손절
  trailing_take_pct: 0.10        # 고점 대비 -10% 트레일링 익절
  graduated_mdd: [0.10, 0.12, 0.15]  # 25/50/75% 단계적 trim
```

### 합의 (강세장에 quant 우대 — 사용자 명시 요청)
```yaml
consensus:
  weights: {macro: 0.20, sector: 0.15, value: 0.20, quant: 0.45}
  min_conviction: 5
  min_agreement: 2
```

### 체결 (let winners run)
```yaml
execution:
  rebalance_buy_threshold: 0.04    # 빠른 추가매수
  rebalance_sell_threshold: 0.08   # 늦은 trim
  pyramid_triggers: [0.20, 0.40]   # +20% / +40% 에서 추가매수
  pyramid_step_pct: 0.025
  shock_action: scale              # 야간 EWY -2% 시 절반축소
```

### Quant 팩터 (regime-conditional, 코드 단계)
`src/agents/quant_agent.py::REGIME_WEIGHTS` 직접 수정. risk_on에서 momentum=0.35 + breakout=0.15 + flow=0.15.

전체 옵션은 `config/settings.yaml`에 인라인 주석으로 설명되어 있습니다.

---

## 8. 트러블슈팅

### KIS 인증 실패 / RetryError
1. `mais doctor` 실행
2. KIS Open API 포털에서 키 만료 여부 확인
3. `.kis_token.json` 삭제 후 재시도 (토큰 캐시 무효화)

### 매일 09:05에 주문이 안 나감
- `is_trading_day(today)` 가 false 인지 확인 (한국 임시휴장 등): `mais doctor`
- KIS_ENV가 paper인데도 안 나가면 → 사실 paper에서도 모의주문이 들어가야 정상. settings의 `dry_run_default` 확인
- overnight shock 게이트 발동 여부: Alerts 페이지에서 "skip_due_to_shock" 검색

### 대시보드 빈 화면 (포지션 없음)
- 정상. `▶ 지금 첫 리서치 실행` 버튼 클릭
- 60~120초 대기 → Today 페이지로 이동

### LLM 에이전트가 매번 실패
- `ANTHROPIC_API_KEY` 만료 또는 잔액 부족
- 토큰 사용량은 `/metrics` 엔드포인트(http://localhost:9100) 또는 Prometheus의 `mais_llm_tokens_total`로 확인

### MDD 가드가 너무 자주 발동
- `risk.graduated_mdd: [0.12, 0.15, 0.18]` 로 완화
- 또는 `risk.cash_buffer_min: 0.03` 으로 더 공격적으로

### 비상 청산
**대시보드**: 🎛️ Control → Kill switch → "I-UNDERSTAND" 입력 → 빨간 버튼
**CLI**: `docker compose run --rm scheduler python scripts/kill_switch.py --confirm I-UNDERSTAND`

---

## 9. 백업 / 복구

### 자동 백업 권장
crontab 또는 외부 스케줄러로:
```bash
docker compose run --rm scheduler mais backup
# 결과: data_store/backups/<timestamp>.tar.gz
```

### 복구
```bash
docker compose run --rm scheduler mais restore data_store/backups/20260101.tar.gz --confirm YES
```

### 무엇이 들어있나
- `journal.sqlite` — 모든 의사결정 + outcomes (가장 중요)
- `chroma/` — RAG 임베딩
- `state/` — 일일 타깃
- `reflections/` — 주간 리포트
- `ab_books/` — paper book NAV 히스토리
- `nav_history.csv` — equity curve
- `bandit_twap.json` — TWAP arm 보상

`logs/`는 회전되므로 백업 제외 OK (또는 별도 보관).

---

## 10. 개발 환경 (코드 수정 시)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pre-commit install
pytest -q              # 180개 테스트
ruff check src tests
```

**커밋 컨벤션** (모든 청크가 따름):
- 한 줄 요약: 무엇을 + 왜 (예: "Add KRX tick-size snapping (P0-bug)")
- 본문: 변경 내용 + 새 의존성 + 호환성
- 마지막 줄: 세션 URL (있으면)

---

## 11. 의존성 매트릭스 (런타임 핵심만)

| 패키지 | 역할 | 실패 시 영향 |
|---|---|---|
| anthropic | LLM 호출 | 에이전트 동작 불가 |
| pykrx | KOSPI 데이터 | universe → fallback 20종, 팩터 → empty |
| FinanceDataReader | 백업 가격 소스 | OK |
| yfinance | 매크로 + EWY | 매크로 stub, overnight gate 항상 false |
| feedparser | 뉴스 RSS | 헤드라인 수집 X |
| chromadb | RAG | TF-IDF fallback |
| sklearn | ML booster | mean fallback |
| websockets | 실시간 stops | 비활성화됨 (옵션 기능) |
| prometheus-client | 메트릭 | no-op stub |

**모든 패키지가 graceful fallback** 되도록 설계되어 있어, 일부 누락이 시스템 전체를 다운시키지 않습니다.

---

## 12. 문서 인덱스

| 문서 | 용도 |
|---|---|
| `README.md` | 1분 요약 + 빠른 시작 |
| `docs/HANDOVER.md` | **(이 문서)** 인수인계용 종합 |
| `CHANGELOG.md` | 청크별 변경 이력 |
| `docs/architecture.md` | 데이터 흐름 다이어그램 |
| `docs/agents.md` | 에이전트 계약 + 프롬프트 가이드 |
| `docs/risk_policy.md` | 리스크 한도 + 비상 절차 |
| `docs/docker.md` | Windows Docker 운영 |
| `docs/improvements.md` | 26개 개선 항목 트래커 |
| `docs/MODULES.md` | src/ 모듈 한 줄 설명 |
| `CLAUDE.md` | Claude Code 가이드 (개발자용) |

---

## 13. 알려진 한계 (정직하게)

- **LLM 응답 품질**은 시스템이 보장 안 함 — 매주 reflection으로 attribution 모니터 필수
- **KIS 모의투자 환경의 시세는 실시간이 아님** — paper 백테스트는 참고용
- **공휴일 이외 임시휴장**은 pykrx가 제공하지 않으면 운영자가 settings에서 수동 처리
- **세금 모델은 단순화**되어 있음 (매도 23bps 일률) — 실제 양도소득세는 회계처리 별도
- **포지션이 50개 이상**이면 KIS API 호출량이 분당 한도에 근접 — rate limiter가 처리하지만 응답 지연 가능

---

## 14. 다음 단계 — 실전 라이브로 가기 전 체크리스트

- [ ] paper 환경에서 **2주 이상** 무사고 운영
- [ ] morning report 매일 도착 확인
- [ ] 가드 한 번 이상 발동 + 정상 동작 확인
- [ ] reflection 리포트 1개 이상 생성
- [ ] `mais doctor` 모든 항목 OK
- [ ] `data_store/` 백업 절차 확립
- [ ] Slack alert 응답 테스트 (Kill switch까지 5분 안에 도달 가능?)
- [ ] **소액(100~200만원)부터 시작** — `KIS_ENV=live` + 작은 NAV
- [ ] 매일 X-ray 페이지에서 factor exposure drift 모니터

---

## 15. 도움 받기

- 코드: `docs/MODULES.md` 에서 어느 파일이 어디에 있는지 찾기
- 운영: `mais doctor` 가 첫 진단
- 변경 이력: `CHANGELOG.md`
- 새 기능 추가 아이디어: `docs/improvements.md` "Future ideas" 섹션

---

**End of HANDOVER. 행운을 빕니다.**
