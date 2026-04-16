# KOSPI Research Agent

한국 주식시장(KOSPI/KOSDAQ) 전용 리서치 에이전트. **매 거래일 07:00 KST**에 자동으로 실행되어:

1. **pykrx**를 통해 KOSPI(또는 KOSDAQ/전체) 전종목의 OHLCV + 시가총액을 가져옴
2. 일일 스냅샷을 누적 저장하여 **2거래일 정확한 등락률**을 계산
3. ETF/ETN/우선주/리츠/스팩을 제외하고, 거래대금 10억 이상인 종목 중 **상승률 Top 5**를 선별
4. **네이버 금융** 뉴스 헤드라인을 수집하고, **Claude Sonnet 4.6**(프롬프트 캐싱 적용)으로 상승 원인을 분석
5. 최근 7일치 보고서를 기반으로 **시장 내러티브**(핫/쿨링 섹터, 섹터 로테이션, 투자 인사이트)를 종합
6. JSON 보고서를 `docs/reports/`에 저장하고 **GitHub Pages** 대시보드에 반영
7. **텔레그램**으로 요약 + 대시보드 딥링크를 발송
8. 오래된 스냅샷(30일+)을 자동 정리

서버 없이 **GitHub Actions cron**으로 완전 자동화.

---

## 아키텍처

```
GitHub Actions (cron: 22:00 UTC = 07:00 KST, 평일만)
        │
        ▼
┌────────────┐   ┌──────────────┐   ┌──────────────────┐
│ Fetcher    │──▶│ Ranker       │──▶│ Analyzer (Claude)│──┐
│ pykrx/KRX  │   │ 2거래일 Top5 │   │  + 네이버 뉴스    │  │
└────────────┘   └──────────────┘   └──────────────────┘  │
        │                                                  ▼
        ▼                                     ┌──────────────────┐
┌────────────────┐                             │ Narrative (Claude)│
│ Snapshots      │◀── loaded by ranker ────────│  7일 종합 분석    │
│ (2거래일 전)    │                             └─────────┬────────┘
└────────────────┘                                        │
                                                          ▼
                                          ┌─────────────────────────┐
                                          │ docs/reports/*.json     │ → GitHub Pages
                                          └─────────────────────────┘
                                                          │
                                                          ▼
                                                 ┌──────────────┐
                                                 │ Telegram Bot │
                                                 │ + deep link  │
                                                 └──────────────┘
```

## 디렉토리 구조

```
.
├── src/
│   ├── main.py               # 파이프라인 엔트리 포인트
│   ├── fetcher.py            # pykrx KRX 데이터
│   ├── ranker.py             # 2거래일 상승률 Top-K
│   ├── news.py               # 네이버 금융 뉴스
│   ├── analyzer.py           # Claude 개별 분석 (프롬프트 캐싱)
│   ├── narrative.py          # 주간 내러티브 종합
│   ├── notifier.py           # 텔레그램 MarkdownV2
│   ├── storage.py            # 스냅샷 + 보고서
│   ├── config.py             # 환경변수 기반 Settings
│   ├── models.py             # Pydantic 스키마
│   └── logging_setup.py
├── prompts/
│   ├── analyzer_system.md    # 한국 주식 분석 프롬프트
│   └── narrative_system.md   # 한국 시장 내러티브 프롬프트
├── data/snapshots/           # 일일 스냅샷 (종가, 시총, 거래대금)
├── docs/                     # ── GitHub Pages 루트 ──
│   ├── index.html            # 대시보드
│   ├── report.html           # 날짜별 보고서
│   ├── assets/{app.js, style.css}
│   └── reports/
├── tests/
├── .github/workflows/daily.yml
├── .env.example
└── pyproject.toml
```

## 셋업 (최초 1회)

### 1. 새 GitHub 레포지토리 생성

github.com에서 빈 레포: `<your-user>/kospi-research-agent`

### 2. 코드 이관

```bash
cd kospi-research-agent
git init -b main
git add .
git commit -m "Initial import: KOSPI research agent"
git remote add origin https://github.com/<your-user>/kospi-research-agent.git
git push -u origin main
```

### 3. GitHub Pages 활성화

Repository → **Settings** → **Pages** → Source: **GitHub Actions**.

### 4. Secrets 등록

Repository → **Settings** → **Secrets and variables** → **Actions**.

| 구분 | 이름 | 필수 | 값 |
|---|---|---|---|
| Secret | `ANTHROPIC_API_KEY` | ✅ | `sk-ant-…` |
| Secret | `TELEGRAM_BOT_TOKEN` | ✅ | @BotFather 발급 |
| Secret | `TELEGRAM_CHAT_ID` | ✅ | 채팅 ID |
| Variable | `DASHBOARD_URL` | ✅ | `https://<user>.github.io/kospi-research-agent/` |
| Variable | `MARKET` | 선택 | `KOSPI` (기본), `KOSDAQ`, `ALL` |

**텔레그램 채팅 ID 확인**:
1. [@BotFather](https://t.me/BotFather)에서 봇 생성, 토큰 저장
2. 봇에게 아무 메시지 전송
3. `https://api.telegram.org/bot<TOKEN>/getUpdates`에서 `chat.id` 확인

### 5. 첫 실행

Actions → **Daily KOSPI Research** → **Run workflow**

첫 실행은 이전 스냅샷이 없으므로 **전일 대비(1거래일)** 기준으로 동작하고,
3번째 실행부터 정확한 **2거래일 기준**이 적용됩니다.

## 로컬 실행

```bash
pip install -e ".[dev]"
cp .env.example .env     # 시크릿 입력

# dry run: KRX 데이터만 조회 + 순위 (LLM/텔레그램 스킵)
python -m src.main --dry-run

# full run, 텔레그램 스킵
python -m src.main --skip-telegram

# full run
python -m src.main
```

테스트 + 린트:

```bash
python -m pytest
python -m ruff check src tests
```

대시보드 로컬 프리뷰:

```bash
python -m http.server --directory docs 8000
# http://localhost:8000
```

## Cron 스케줄

- **07:00 KST = 22:00 UTC (전일)**
- **평일만**: `0 22 * * 0-4` (일~목 UTC = 월~금 KST)
- 주말/공휴일은 KRX 데이터가 없으므로 스냅샷만 스킵됨

## 필터링 로직

| 항목 | 기준 |
|---|---|
| ETF/ETN | 종목명에 KODEX, TIGER, KBSTAR 등 패턴 |
| 우선주 | 종목명 끝 "우", "우B", "우(전환)" |
| 스팩/리츠 | 종목명에 SPAC, 스팩, 리츠 |
| 최소 거래대금 | 10억 원 이상 (`MIN_VOLUME_KRW`) |
| 상승률 기준 | 2거래일 전 스냅샷 대비 (콜드스타트 시 전일 대비) |

## 비용

- **pykrx**: 무료 (KRX 공식 데이터)
- **네이버 금융**: 무료 (스크래핑)
- **Claude Sonnet 4.6**: 1일 ~2회 호출 (분석 1 + 내러티브 1). 프롬프트 캐싱 적용.
  예상 비용: **~₩40–100/일** (~$0.03–0.08)

## 환경변수 튜닝

| 변수 | 기본값 | 설명 |
|---|---|---|
| `MARKET` | `KOSPI` | `KOSPI`, `KOSDAQ`, `ALL` |
| `TOP_K_GAINERS` | `5` | 선별 종목 수 |
| `MIN_VOLUME_KRW` | `1000000000` | 최소 거래대금 (원) |
| `LOOKBACK_TRADING_DAYS` | `2` | 비교 대상 거래일 수 |
| `NARRATIVE_LOOKBACK_DAYS` | `7` | 내러티브 참조 일수 |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Claude 모델 |
