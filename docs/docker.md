# Docker — Windows 로컬 운영 가이드

이 문서는 Windows 11 + Docker Desktop 환경에서 MAI-System을 실제로 운영하기
위한 설정을 다룹니다.

## 사전 준비

### 필수 설치

| 도구 | 설치 방법 | 비고 |
|---|---|---|
| Docker Desktop | https://docs.docker.com/desktop/install/windows/ | WSL2 백엔드 권장 |
| Git for Windows | https://git-scm.com/download/win | |
| Windows Terminal | Microsoft Store 검색 "Windows Terminal" | 권장 |

### WSL2 활성화 (권장, Docker Desktop 설치 전에 수행)

```powershell
# 관리자 PowerShell에서 실행
wsl --install
# 재부팅 후 Docker Desktop에서 Settings → General → "Use WSL 2 based engine" 체크
```

### 계정/키 발급

- **한국투자증권 KIS Open API**: https://apiportal.koreainvestment.com/ → 모의투자 신청
- **Anthropic API Key**: https://console.anthropic.com/ → Claude 4 사용 가능 플랜
- **DART API Key** (선택): https://opendart.fss.or.kr/
- **ECOS API Key** (선택): https://ecos.bok.or.kr/

---

## 1) 자동 설치 스크립트 (권장)

루트 폴더의 `setup_windows.ps1`이 아래 모든 단계를 자동으로 수행합니다.

```powershell
# 1. 코드 클론
git clone <repository_url> mais
cd mais
git checkout claude/setup-windows-environment-J0O1g

# 2. PowerShell 실행 정책 일시 허용 (필요 시)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 3. 자동 설치
.\setup_windows.ps1
```

스크립트가 하는 일:
- Docker / Git / WSL2 존재 여부 검증
- `.env` 파일 대화형 작성 (API 키 입력)
- `data_store/` 하위 디렉토리 생성
- `.kis_token.json` 빈 캐시 파일 생성
- `docker compose build` (5~10분)
- `mais doctor` 환경 진단
- `docker compose up -d scheduler dashboard` + 브라우저 자동 오픈

---

## 2) 수동 설치 (스크립트 대신)

```powershell
# 1. 코드 체크아웃 후 폴더로 이동
cd C:\dev\mais

# 2. 환경변수 파일 작성
copy .env.example .env
notepad .env   # KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO / ANTHROPIC_API_KEY 입력

# 3. 빈 토큰 캐시 파일 (KIS 토큰 보관용)
type nul > .kis_token.json

# 4. 데이터 디렉토리 생성
mkdir data_store\logs, data_store\state, data_store\reflections

# 5. 이미지 빌드 + 환경 진단
docker compose build
docker compose run --rm scheduler mais doctor

# 6. 백그라운드 기동 + 대시보드 확인
docker compose up -d scheduler dashboard
start http://localhost:8501
```

## 2) 운영 모드

| 명령 | 동작 |
|---|---|
| `docker compose up -d scheduler dashboard` | 자동 스케줄러 + 대시보드 상시 운영 |
| `docker compose run --rm daily` | 오늘자 파이프라인 1회 실행 (모의·dry-run) |
| `docker compose run --rm backtest` | 백테스트 실행 (Quant-only) |
| `docker compose run --rm reflect` | 리플렉션 리포트 생성 |
| `docker compose down` | 컨테이너 정지 |
| `docker compose logs -f scheduler` | 스케줄러 로그 추적 |

## 3) 실거래로 전환하기

`.env`에서 `KIS_ENV=live`로 바꾸면 KIS 라이브 환경으로 주문이 나갑니다.
**반드시** 다음 절차를 따르세요:

1. 모의투자(paper) 환경에서 최소 2주 무사고 운영
2. `config/settings.yaml::risk` 한도 재확인
3. 소액(예: 1천만 원) 부터 시작
4. `scheduler.order_time` 직전에는 콘솔 로그를 모니터링

긴급 청산이 필요하면:

```powershell
docker compose run --rm scheduler python scripts/kill_switch.py --confirm I-UNDERSTAND
```

## 4) 자주 만나는 이슈

- **`pykrx` SSL 오류**: KRX 서버 일시적 차단. 컨테이너 재기동으로 대부분 해결.
- **`ANTHROPIC_API_KEY is not set`**: `.env` 누락 또는 줄 끝의 따옴표 문제.
- **8501 포트 충돌**: `docker-compose.yml`의 `8501:8501`을 `8888:8501` 등으로 변경.
- **시간대가 UTC로 보임**: `MAIS_TZ=Asia/Seoul`과 컨테이너의 `TZ`가 `.env`에서
  덮어쓰여지지 않는지 확인.

## 5) 데이터 보존

볼륨 매핑:

```
./data_store    → /app/data_store    # SQLite, Chroma, 리플렉션 리포트
./config        → /app/config        # 프롬프트 / 설정
./.kis_token.json → /app/.kis_token.json  # OAuth 캐시
```

`data_store/`는 **반드시 백업**하세요. 의사결정 저널이 없으면 반성·자가학습이
불가능합니다.

## 6) 자동 시작 (Windows 부팅 시)

Docker Desktop 설정 → **General** → "Start Docker Desktop when you log in"
체크. compose 서비스에 `restart: unless-stopped`가 걸려 있으므로 재부팅 후
자동으로 다시 올라옵니다.
