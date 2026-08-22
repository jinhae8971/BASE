# 업비트 알트코인 데이트레이딩 자동매매

KRW 마켓 알트코인을 대상으로 **매일 아침 09:10 (KST)** 에 종목을 선정해 자동으로 매수하고,
장중 익절·손절·트레일링·보유시간 조건에 따라 자동으로 청산하는 서브시스템이다.
KIS 기반 국내주식 스택(MAIS)과는 완전히 독립적으로 동작한다.

> **설계 의도** — 다가올 상승장에서 알트코인 펌핑의 **베타 수익**을 취하는 것이 목표다.
> 그래서 종합점수의 4번째 축이 `beta`(BTC 대비 베타 + 상대강도)이고, BTC 국면이 위험선호일 때
> 고베타·상대강도 우위 종목에 가중치를 더 준다. 반대로 위험회피 국면에서는 신규 진입이 0이 된다.

---

## 1. 구성

```
src/upbit/
├── client.py       업비트 REST 클라이언트 (JWT 인증, 레이트리밋, 재시도)
├── credentials.py  API 키 암호화 저장 (Fernet, data_store/ 아래)
├── indicators.py   RSI · MACD · 볼린저 · ATR · OBV · MFI · 베타
├── scoring.py      종합점수 4축 + BTC 국면 판정
├── universe.py     KRW 마켓 스크리닝 (유의/주의/스테이블/유동성/장기보유)
├── holdings.py     장기보유 코인 보호 가드
├── strategy.py     전략 설정 (YAML 기본값 + 대시보드 오버라이드)
├── risk.py         포지션 사이징 · 노출 한도 · 일일 손실 차단
├── broker.py       PaperBroker (모의) / LiveBroker (실거래)
├── engine.py       스캔 → 진입 → 모니터링 → 청산
├── scheduler.py    APScheduler (09:10 선정, N분 모니터링, EOD 청산)
├── store.py        SQLite (거래·분석·포지션·자산·설정·로그)
└── cli.py          mais-upbit CLI

src/dashboard/
├── server.py       FastAPI 백엔드 (REST API)
└── static/         SPA 프론트엔드 (7개 탭, 외부 CDN 의존 없음)
```

---

## 2. 종합점수 (0~100)

매일 아침 스캔에서 각 코인을 4개 축으로 평가하고 가중 합산한다. 가중치는 대시보드 **전략** 탭에서
슬라이더로 조절할 수 있으며 합계는 자동 정규화된다.

| 축 | 기본 가중치 | 측정 내용 |
|---|---|---|
| **거래량** | 0.30 | 24h 거래대금 / 20일 평균 (급증배수), 5일·20일 추세, Z-score, 절대 유동성 |
| **수급** | 0.25 | 호가 매수/매도 불균형(전체·상위5호가), 체결 강도(taker 매수비중), MFI(14), OBV 기울기 |
| **차트** | 0.30 | MA 정렬(5/20/60), RSI·MACD 히스토그램, 20일 레인지 위치·고점 이격·볼린저 %B, ATR 적합도, 60분봉 확인 |
| **베타** | 0.15 | BTC 대비 30일 베타, 7일 상대강도, 30일 지속성 |

각 지표는 구간 정규화(`_ramp` 선형 램프, `_band` 사다리꼴)를 거친다. `_band` 는 **"더 많을수록 좋다"가
성립하지 않는** 지표에 쓴다 — 이미 소진된 RSI(>85)나 손절이 노이즈에 걸릴 만큼 큰 변동성은 감점된다.

모든 중간 지표는 `analyses.metrics_json` 에 저장되어 대시보드 **분석내역** 탭에서 사후 검증할 수 있다.

### BTC 국면 게이트

스캔마다 BTC 일봉으로 시장 국면을 먼저 판정한다.

| 국면 | 조건 | 노출 배수 |
|---|---|---|
| `risk_on` | 20/50일선 위 + RSI ≥ 50 + 7일 수익률 > 0 중 3개 이상 | 1.0 |
| `neutral` | 혼조 | 0.6 |
| `risk_off` | 위 조건 중 1개 이하 | **0.0 (신규 진입 차단)** |

`risk_off` 로 전환되면 신규 진입이 막히고, 모니터링 사이클에서 기존 포지션도 청산된다.

---

## 3. 유니버스 스크리닝

1. KRW 마켓만
2. **유의 종목** 제외
3. **주의 종목** — 유형별로 선택 배제 (`universe.caution_types`)
4. 스테이블코인 제외
5. 수동 제외 목록
6. **장기보유 코인 제외** ← 아래 4장
7. 24h 거래대금 하한/상한, 최소 가격
8. 거래대금 상위 N개만 정밀 분석

> ⚠️ **주의 유형에 대한 설계 판단**
> 업비트의 '주의' 플래그에는 `TRADING_VOLUME_SOARING`(거래량 급증)이 포함된다. 그런데 거래량 급증은
> 이 전략의 **진입 신호 그 자체**다. 전체 주의 종목을 일괄 배제하면 노리는 종목이 대부분 걸러진다
> (실측: 유니버스 43 → 30종목). 그래서 기본값은 실제 위험 신호만 배제한다:
> `PRICE_FLUCTUATIONS`, `DEPOSIT_AMOUNT_SOARING`, `GLOBAL_PRICE_DIFFERENCES`,
> `CONCENTRATION_OF_SMALL_ACCOUNTS`.
> 보수적으로 운용하려면 전략 탭에서 `TRADING_VOLUME_SOARING` 을 목록에 추가하면 된다.

### 2단계 스캔

60종목 × 4개 API = 240 콜을 피하기 위해 2단계로 나눈다.

- **1단계** — 일봉만으로 거래량·차트·베타 계산 (전 종목)
- **2단계** — 상위 `max_positions × 3`(최소 10)종목만 호가·체결·60분봉을 추가 조회해 수급까지 계산

실측 전체 스캔 소요: **약 15~19초**.

---

## 4. 장기보유 코인 제외

대시보드 **장기보유** 탭(또는 `mais-upbit holdings add BTC`)에 등록한 코인은 두 가지가 보장된다.

1. **절대 매수하지 않는다** — 유니버스 스크리닝에서 제거되고, 진입 루프에서 한 번 더 확인한다.
2. **절대 매도하지 않는다** — 모든 매도는 `잔고 - 보호수량` 으로 상한이 걸린다. 전량 청산(킬 스위치)과
   EOD 강제 청산도 예외가 아니다.

| 잠금 수량 | 동작 |
|---|---|
| `0` | 보유 **전량** 보호 (일반적인 "BTC는 손대지 마") |
| `> 0` | 그 수량만 보호, 초과분은 매도 가능. 단 신규 매수 대상에서는 계속 제외 |

**엔진이 이미 보유 중인 코인을 뒤늦게 장기보유로 등록하면**, 그 포지션은 매도되지 않고
`long_term_protected` 사유로 **관리만 해제**된다. 코인은 계좌에 그대로 남고 포지션 슬롯만 회수된다.

자산 계산에서도 장기보유분은 분리된다 — 포지션 사이징의 기준이 되는 `tradable_equity` 는
현금 + 엔진 포지션이며, 장기보유 평가액은 여기에 포함되지 않는다.

---

## 5. 진입 · 청산 규칙

### 진입 (매일 09:10)

순위대로 아래 게이트를 통과하는 동안 매수한다.

1. 국면 게이트 (`risk_off` 면 전면 차단)
2. 일일 손실 한도 — 오늘 실현손실이 `daily_loss_kill_pct` 초과 시 신규 진입 중단
3. 노출 한도 — `max_total_exposure_pct` 및 `min_cash_buffer_pct`
4. 보유 종목 수 — `max_positions`
5. 최소 점수 — `min_score` 미만이면 이후 순위는 전부 탈락(정렬되어 있으므로 조기 종료)

주문액 = `tradable_equity × position_pct × 국면배수 × 확신도 틸트`
(확신도 틸트: 기준점수에서 0.85배, 100점에서 1.15배)

### 청산 (기본 5분 간격)

| 사유 | 조건 |
|---|---|
| `take_profit` | 현재가 ≥ 진입가 × (1 + `take_profit_pct`) |
| `stop_loss` | 현재가 ≤ 진입가 × (1 − `stop_loss_pct`) |
| `trailing_stop` | 고점 대비 `trailing_gap_pct` 하락 (고점이 `trailing_activate_pct` 이상일 때만 발동) |
| `time_exit` | 보유 시간 ≥ `max_hold_hours`, 또는 EOD 강제 청산 시각 |
| `regime_exit` | BTC 국면이 `risk_off` 로 전환 |

---

## 6. 설치와 실행

실행 방법은 두 가지다. **장기 운용이라면 도커를 권한다** — 호스트에 파이썬을 깔지 않고,
크래시나 재부팅에도 자동으로 복구된다. 잠깐 써보거나 코드를 만질 거라면 파이썬 직접 설치가 편하다.

### 원클릭 실행 (파이썬 직접 설치)

저장소를 받은 폴더에서 아래 하나만 실행하면 됩니다. 가상환경 생성 → 의존성 설치 →
환경 점검 → 대시보드 실행 → 브라우저 열기까지 전부 처리합니다.
두 번째 실행부터는 설치를 건너뛰고 바로 뜹니다.

```bash
# macOS / Linux
./start-upbit.sh
./start-upbit.sh --port 9000      # 포트 변경
./start-upbit.sh --reinstall      # 의존성 재설치

# Windows
start-upbit.bat                   # 더블클릭
.\start-upbit.ps1 -Port 9000      # PowerShell
```

> Windows 에서 `.ps1` 실행이 막히면 `start-upbit.bat` 을 쓰거나
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` 를 먼저 실행하세요.

### 수동 설치

의존성은 서브시스템별로 분리되어 있습니다. `[upbit]` 는 국내주식 스택(cvxpy, pykrx,
chromadb 등)을 전혀 설치하지 않습니다.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[upbit]"          # 개발까지 하려면 ".[upbit,dev]"

mais-upbit doctor                  # 실행 전 점검
mais-upbit serve                   # 대시보드 + 스케줄러
```

### 실행 전 점검 (`mais-upbit doctor`)

| 점검 항목 | 내용 |
|---|---|
| Python | 3.11 이상 |
| 의존성 패키지 | 런타임에 필요한 14개 |
| 설정 파일 | `config/settings.yaml` 및 `upbit` 섹션 |
| 대시보드 파일 | `src/dashboard/static/` |
| 데이터 저장소 | `data_store/` 쓰기 권한, SQLite 초기화 |
| 거래 모드 / 스케줄 | paper·live, 09:10 선정 시각 등 |
| 장기보유 제외 | 등록된 코인 (없으면 경고) |
| API 키 | 등록 여부 (없으면 경고 — 모의는 동작) |
| 업비트 공개/계좌 API | 실제 연결 및 인증 확인 |
| 대시보드 포트 | 점유 여부, 외부 노출 시 토큰 유무 |

`FAIL` 이 있으면 `serve` 가 실행을 거부합니다 (`--skip-checks` 로 우회 가능).

### CLI

```bash
mais-upbit status                  # 모드·자산·포지션·보호 심볼
mais-upbit scan --dry-run          # 점수만 계산, 주문 없음
mais-upbit scan --execute          # 실제 진입 (모드에 따라 모의/실거래)
mais-upbit monitor                 # 청산 조건 1회 점검
mais-upbit holdings add BTC        # 거래 제외 등록
mais-upbit liquidate --confirm I-UNDERSTAND
```

### 도커로 운영하기 (권장)

호스트에 파이썬을 깔지 않고, 부팅·크래시 후 자동 복구되는 형태로 돌립니다.

```bash
docker compose up -d --build     # 첫 실행 (빌드 3~5분)
docker compose logs -f           # 로그
docker compose ps                # 상태 / healthcheck
docker compose down              # 정지 (데이터는 볼륨에 남습니다)
```

대시보드: <http://127.0.0.1:8787>

| 특성 | 값 |
|---|---|
| 베이스 | `python:3.12-slim` · 이미지 약 450MB |
| 실행 계정 | 비루트 `upbit` (uid 10001) |
| 타임존 | `Asia/Seoul` (엔진은 별도로 KST 고정) |
| 포트 게시 | `127.0.0.1:8787` — 같은 PC 에서만 접근 |
| 재시작 | `unless-stopped` — 크래시·부팅 시 자동 복구, 직접 `stop` 하면 그대로 유지 |
| 헬스체크 | 30초 간격 `/api/health` |
| 로그 | json-file, 10MB × 5 로 순환 |
| 데이터 | named volume `upbit-data` → `/app/data_store` |

#### 컨테이너 안에서 CLI 쓰기

```bash
docker compose exec upbit mais-upbit status
docker compose exec upbit mais-upbit doctor
docker compose exec upbit mais-upbit holdings add BTC --memo "장투"
docker compose exec upbit mais-upbit scan --dry-run
docker compose exec upbit mais-upbit liquidate --confirm I-UNDERSTAND   # 킬 스위치
```

#### 백업과 복원 — 반드시 해두세요

`upbit-data` 볼륨에는 **암호화된 API 키(`upbit_credentials.enc`), 그 복호화용 마스터 키
(`.upbit_master.key`), 전체 거래·분석 이력(`upbit.sqlite`)** 이 들어 있습니다.
볼륨을 지우면 전부 사라집니다. `docker compose down -v` 는 볼륨까지 삭제하니 주의하세요.

```bash
# 백업 (숨김 파일 포함)
docker run --rm -v upbit-data:/data -v "$PWD":/backup alpine:3   tar czf /backup/upbit-backup-$(date +%Y%m%d).tar.gz -C /data .

# 복원
docker compose down
docker run --rm -v upbit-data:/data -v "$PWD":/backup alpine:3   sh -c "rm -rf /data/* /data/.[!.]* 2>/dev/null; tar xzf /backup/upbit-backup-20260822.tar.gz -C /data"
docker compose up -d
```

> Windows PowerShell 에서는 `$PWD` 대신 `${PWD}` 를 쓰고 `$(date ...)` 는 직접 날짜를 적으세요.

#### 설정 바꾸기

* **전략 값** — 대시보드 전략 탭에서 바꾸면 즉시 반영됩니다 (DB 오버라이드, 재시작 불필요).
* **기본값 파일** — `config/settings.yaml` 이 읽기 전용으로 마운트되어 있어 호스트에서 편집 후
  `docker compose restart` 하면 반영됩니다. 단 대시보드에서 바꾼 값이 이 파일보다 우선합니다.
* **포트 변경** — compose 의 `ports` 를 `127.0.0.1:9000:8787` 로 바꾸고 `docker compose up -d`.
* **암호화 키를 디스크에 두지 않기** — 저장소 루트 `.env` 에 `UPBIT_KEY_PASSPHRASE=...` 를 넣으면
  마스터 키를 그 문구에서 유도합니다. 잊어버리면 저장된 API 키를 복구할 수 없습니다.

#### LAN 의 다른 기기에서 열기

기본은 이 PC 에서만 열립니다. 휴대폰 등에서 보려면 **반드시 토큰을 함께 설정**하세요.
주문 실행 권한이 그대로 노출됩니다.

```yaml
# docker-compose.yml
ports:
  - "8787:8787"        # 127.0.0.1 접두사 제거
```
```bash
# 저장소 루트 .env
UPBIT_DASHBOARD_TOKEN=충분히-긴-임의-문자열
```
브라우저에서는 개발자도구 콘솔에 아래를 한 번 실행해 토큰을 저장합니다.
```js
localStorage.setItem('upbit_dashboard_token', '충분히-긴-임의-문자열')
```

#### 문제가 생기면

```bash
docker compose logs --tail 100        # 기동 로그와 사전 점검 결과
docker compose exec upbit mais-upbit doctor
docker compose restart
docker compose up -d --build          # 코드를 받은 뒤 재빌드
```

기동 시 사전 점검은 **실행을 막아야 하는 실패**(의존성 누락, 저장소 쓰기 불가, 포트 점유)에서만
중단합니다. API 키가 거부되는 경우처럼 **대시보드에서 고칠 수 있는 문제**는 실패로 표시하되
그대로 기동합니다 — 키를 다시 넣을 화면이 안 뜨면 고칠 방법이 없기 때문입니다.

### 24시간 켜두기 (도커를 안 쓸 때)

대시보드 프로세스가 스케줄러를 함께 들고 있으므로, **이 프로세스만 살아 있으면**
매일 09:10 선정과 5분 간격 모니터링이 자동으로 돕니다. PC를 껐다 켜도 자동으로
뜨게 하려면:

**Windows — 작업 스케줄러**
1. `작업 스케줄러` → `기본 작업 만들기`
2. 트리거: `로그온할 때`
3. 동작: `프로그램 시작` → 프로그램 `C:\경로\start-upbit.bat`, 시작 위치 `C:\경로`
4. 속성에서 `사용자가 로그온한 경우에만 실행` 유지 (암호 저장 불필요)

**macOS — launchd** (`~/Library/LaunchAgents/com.upbit.desk.plist`)
```xml
<key>ProgramArguments</key><array>
  <string>/bin/bash</string><string>/경로/start-upbit.sh</string>
</array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>WorkingDirectory</key><string>/경로</string>
```
`launchctl load ~/Library/LaunchAgents/com.upbit.desk.plist`

**Linux — systemd user unit** (`~/.config/systemd/user/upbit-desk.service`)
```ini
[Service]
WorkingDirectory=/경로
ExecStart=/경로/.venv/bin/python -m upbit.cli serve --skip-checks
Restart=always
[Install]
WantedBy=default.target
```
`systemctl --user enable --now upbit-desk`

### 대시보드 없이 cron 으로만

```cron
10 9 * * *  cd /path/to/repo && .venv/bin/python scripts/run_upbit_daily.py scan --execute
*/5 * * * * cd /path/to/repo && .venv/bin/python scripts/run_upbit_daily.py monitor
50 8 * * *  cd /path/to/repo && .venv/bin/python scripts/run_upbit_daily.py eod-exit
```

### API 키 등록

대시보드 **설정** 탭에서 Access Key / Secret Key 를 입력한다. 키는 Fernet 으로 암호화되어
`data_store/upbit_credentials.enc` 에 저장되고, 화면에는 마스킹된 값만 표시된다.
`UPBIT_KEY_PASSPHRASE` 환경변수를 설정하면 마스터 키를 디스크에 두지 않고 PBKDF2 로 유도한다.

업비트 Open API 발급 시 **자산조회 + 주문 권한**과 **서버 IP 허용 등록**이 필요하다.
헤드리스 환경에서는 `UPBIT_ACCESS_KEY` / `UPBIT_SECRET_KEY` 환경변수도 사용할 수 있다.

---

## 7. 대시보드

| 탭 | 내용 |
|---|---|
| **대시보드** | 총자산·평가손익·실현손익·승률 KPI, 자산 추이, 자산 구성, 오늘의 선정 종목, BTC 국면, 이벤트 로그 |
| **포트폴리오** | 트레이딩 포지션(평단·수익률·손절/익절선·수동 청산), 장기보유 현황, 비중 도넛 |
| **거래내역** | 체결 내역 필터·CSV 내보내기, 승률/손익비/수수료, 일별 실현손익, 청산 사유 분포 |
| **분석내역** | 실행 이력 타임라인, 종합점수 순위표, 종목별 4축 상세 + 30여개 세부 지표 + 일봉 차트 |
| **전략** | 가중치 슬라이더, 진입/청산 규칙, 리스크 한도, 스케줄, 유니버스 필터 |
| **장기보유** | 제외 코인 CRUD, 계좌 보유분에서 원클릭 등록 |
| **설정** | API 키 등록/테스트/삭제, 모드 전환, 모의 계좌 초기화, 시스템 로그 |

차트는 외부 CDN 없이 자체 canvas 구현(`static/charts.js`)을 쓴다 — 오프라인에서도 동작해야 하기 때문이다.

---

## 8. 안전장치

1. **기본값은 모의(paper) 모드.** 실거래 전환은 대시보드에서 확인 문구 `I-UNDERSTAND` 입력을 요구하고,
   API 키가 등록되어 있지 않으면 거부된다. `PUT /api/config` 로는 모드를 바꿀 수 없다.
2. **킬 스위치** — 대시보드 좌하단 버튼 또는
   `python scripts/upbit_kill_switch.py --confirm I-UNDERSTAND`. 장기보유 코인은 청산되지 않는다.
3. **일일 손실 차단** — 오늘 실현손실이 한도를 넘으면 신규 진입이 중단된다.
4. **레이트리밋** — 조회 8req/s, 주문 6req/s 로 클라이언트 측에서 제한한다.
5. **대시보드는 127.0.0.1 바인드가 기본.** 외부에 노출하려면 `UPBIT_DASHBOARD_TOKEN` 을 설정한다
   (미설정 시 실행 스크립트가 경고한다).
6. **비밀정보는 커밋되지 않는다** — `data_store/` 는 gitignore 대상이고, API 응답은 항상 마스킹된다.

---

## 9. 데이터 스키마 (`data_store/upbit.sqlite`)

| 테이블 | 내용 |
|---|---|
| `runs` | 스캔·모니터링·청산 사이클 기록 |
| `analyses` | 실행별 전 종목 점수와 세부 지표 |
| `trades` | 모든 주문 (모의 포함), 실현손익·수수료·사유 |
| `positions` | 엔진 관리 포지션 (진입가·손절선·고점·청산사유) |
| `equity_snapshots` | 자산 추이 |
| `app_settings` | 전략 오버라이드, 모의 계좌 원장 |
| `long_term_holdings` | 거래 제외 코인 |
| `event_log` | 운영 로그 |

---

## 10. 한계와 주의

- 이 시스템은 **수익을 보장하지 않는다.** 알트코인 데이트레이딩은 손실 위험이 크며,
  특히 `risk_on` 국면에서 고베타 종목을 선호하는 설계는 하락 전환 시 손실도 그만큼 증폭시킨다.
- 백테스트 엔진이 아직 없다. 파라미터는 충분한 기간의 **모의 운용**으로 검증한 뒤 실거래에 옮길 것.
- 시장가 주문은 얇은 호가에서 슬리피지가 발생한다 (모의 모드는 편도 0.15% 로 가정).
- 업비트 수수료는 KRW 마켓 편도 0.05% 기준이며 `upbit.fee_rate` 로 조정한다.
- 지정가 주문(`order_style: limit`)의 호가 단위 표는 `client.py::_KRW_TICKS` 에 있다.
  업비트가 호가 정책을 바꾸면 갱신이 필요하다. 데이트레이딩 기본값은 체결 우선의 시장가다.
