#Requires -Version 5.1
<#
.SYNOPSIS
    MAI-System Windows 로컬 환경 자동 구축 스크립트

.DESCRIPTION
    HANDOVER.md Section 2의 내용을 Windows PowerShell에서 자동으로 수행합니다.
    - 사전 요구사항 검증 (Docker, Git, WSL2)
    - 데이터 디렉토리 생성
    - .env 파일 대화형 작성
    - Docker 이미지 빌드
    - 컨테이너 기동 + 브라우저 열기

.EXAMPLE
    # 일반 실행 (대화형 설정 포함)
    .\setup_windows.ps1

    # 키 직접 입력 (CI/CD 또는 재설치 시)
    .\setup_windows.ps1 -AnthropicKey "sk-ant-..." -KisAppKey "PSxx..." `
        -KisAppSecret "xxx" -KisAccountNo "12345678-01"
#>

[CmdletBinding()]
param(
    [string]$AnthropicKey    = "",
    [string]$KisAppKey       = "",
    [string]$KisAppSecret    = "",
    [string]$KisAccountNo    = "",
    [string]$KisEnv          = "paper",
    [string]$DartApiKey      = "",
    [string]$SlackWebhook    = "",
    [switch]$SkipBuild,
    [switch]$SkipLaunch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── 색상 출력 헬퍼 ────────────────────────────────────────────────────────────
function Write-Step   { param($msg) Write-Host "`n▶ $msg" -ForegroundColor Cyan }
function Write-OK     { param($msg) Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn   { param($msg) Write-Host "  ⚠ $msg" -ForegroundColor Yellow }
function Write-Fail   { param($msg) Write-Host "  ✗ $msg" -ForegroundColor Red; exit 1 }

function Read-NonEmpty {
    param([string]$Prompt, [string]$Default = "", [switch]$Secret)
    while ($true) {
        $display = if ($Default) { "$Prompt [$Default]" } else { $Prompt }
        if ($Secret) {
            $raw = Read-Host -Prompt $display -AsSecureString
            $val = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                       [Runtime.InteropServices.Marshal]::SecureStringToBSTR($raw))
        } else {
            $val = Read-Host -Prompt $display
        }
        if (-not $val -and $Default) { return $Default }
        if ($val)                    { return $val }
        Write-Host "  값을 입력해야 합니다." -ForegroundColor Yellow
    }
}

# ── 배너 ─────────────────────────────────────────────────────────────────────
Clear-Host
Write-Host @"
╔══════════════════════════════════════════════════════════════╗
║       MAI-System — Windows 로컬 환경 구축 스크립트           ║
║       KOSPI 자동매매 · 5 LLM 에이전트 · KIS Open API         ║
╚══════════════════════════════════════════════════════════════╝
"@ -ForegroundColor Cyan

# ── Step 1: 사전 요구사항 검증 ────────────────────────────────────────────────
Write-Step "1/6  사전 요구사항 검증"

# Docker Desktop
try {
    $dv = (docker version --format "{{.Server.Version}}" 2>$null)
    if ($dv) { Write-OK "Docker Engine $dv" }
    else      { throw }
} catch {
    Write-Fail "Docker Desktop이 실행 중이지 않습니다. 설치 후 기동하세요.`n  https://docs.docker.com/desktop/install/windows/"
}

# Docker Compose V2
try {
    $cv = (docker compose version --short 2>$null)
    Write-OK "Docker Compose $cv"
} catch {
    Write-Fail "docker compose 플러그인을 찾을 수 없습니다. Docker Desktop을 최신 버전으로 업데이트하세요."
}

# Git
try {
    $gv = (git --version 2>$null) -replace "git version ", ""
    Write-OK "Git $gv"
} catch {
    Write-Fail "Git이 없습니다. https://git-scm.com/download/win 에서 설치하세요."
}

# WSL2 (권장, 미설치 시 경고만)
$wslStatus = wsl --status 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-OK "WSL2 사용 가능"
} else {
    Write-Warn "WSL2 미설치 — Docker Desktop은 Hyper-V 모드로 동작합니다 (권장: WSL2 백엔드)"
    Write-Warn "설치: wsl --install   (관리자 PowerShell)"
}

# ── Step 2: .env 파일 생성 ────────────────────────────────────────────────────
Write-Step "2/6  환경변수 설정 (.env)"

$envPath = Join-Path $PSScriptRoot ".env"
if (Test-Path $envPath) {
    $overwrite = Read-Host "  .env 파일이 이미 존재합니다. 덮어쓰시겠습니까? [y/N]"
    if ($overwrite -notmatch "^[yY]$") {
        Write-OK ".env 유지 — 기존 설정을 사용합니다."
        $skipEnv = $true
    }
}

if (-not $skipEnv) {
    Write-Host ""
    Write-Host "  KIS Open API 키는 https://apiportal.koreainvestment.com/ 에서 발급받으세요." -ForegroundColor DarkGray
    Write-Host "  모든 항목은 나중에 .env 파일을 직접 편집해도 됩니다." -ForegroundColor DarkGray
    Write-Host ""

    if (-not $AnthropicKey)  { $AnthropicKey  = Read-NonEmpty "  ANTHROPIC_API_KEY" -Secret }
    if (-not $KisAppKey)     { $KisAppKey     = Read-NonEmpty "  KIS_APP_KEY" }
    if (-not $KisAppSecret)  { $KisAppSecret  = Read-NonEmpty "  KIS_APP_SECRET" -Secret }
    if (-not $KisAccountNo)  { $KisAccountNo  = Read-NonEmpty "  KIS_ACCOUNT_NO (예: 12345678-01)" }

    $kisEnvInput = Read-Host "  KIS_ENV [paper/live, 기본값: paper]"
    if ($kisEnvInput -match "^live$") { $KisEnv = "live" } else { $KisEnv = "paper" }

    if (-not $DartApiKey) {
        $DartApiKey = Read-Host "  DART_API_KEY (선택, Enter 건너뜀)"
    }
    if (-not $SlackWebhook) {
        $SlackWebhook = Read-Host "  SLACK_WEBHOOK_URL (선택, Enter 건너뜀)"
    }

    $envContent = @"
# ===== LLM =====
ANTHROPIC_API_KEY=$AnthropicKey
CLAUDE_REASONING_MODEL=claude-opus-4-6
CLAUDE_FAST_MODEL=claude-haiku-4-5-20251001

# ===== 한국투자증권 KIS Open API =====
KIS_APP_KEY=$KisAppKey
KIS_APP_SECRET=$KisAppSecret
KIS_ACCOUNT_NO=$KisAccountNo
KIS_ENV=$KisEnv

# ===== Data sources =====
DART_API_KEY=$DartApiKey
ECOS_API_KEY=

# ===== Runtime =====
MAIS_DATA_DIR=./data_store
MAIS_LOG_LEVEL=INFO
MAIS_TZ=Asia/Seoul

# ===== Notifications (optional) =====
SLACK_WEBHOOK_URL=$SlackWebhook
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
"@
    Set-Content -Path $envPath -Value $envContent -Encoding UTF8
    Write-OK ".env 생성 완료"

    if ($KisEnv -eq "live") {
        Write-Warn "KIS_ENV=live 설정됨 — 실거래 주문이 나갑니다!"
        Write-Warn "paper로 2주 이상 테스트 후 라이브 전환을 권장합니다."
    }
}

# ── Step 3: 디렉토리 + 토큰 캐시 ─────────────────────────────────────────────
Write-Step "3/6  데이터 디렉토리 + KIS 토큰 캐시 파일 생성"

$dirs = @(
    "data_store\logs",
    "data_store\state",
    "data_store\reflections",
    "data_store\chroma",
    "data_store\ab_books",
    "data_store\backups"
)
foreach ($d in $dirs) {
    $full = Join-Path $PSScriptRoot $d
    if (-not (Test-Path $full)) {
        New-Item -ItemType Directory -Path $full -Force | Out-Null
    }
}
Write-OK "data_store/ 하위 디렉토리 준비"

$tokenPath = Join-Path $PSScriptRoot ".kis_token.json"
if (-not (Test-Path $tokenPath)) {
    Set-Content -Path $tokenPath -Value "{}" -Encoding UTF8
    Write-OK ".kis_token.json 생성 (빈 토큰 캐시)"
} else {
    Write-OK ".kis_token.json 이미 존재"
}

# ── Step 4: Docker 이미지 빌드 ────────────────────────────────────────────────
if (-not $SkipBuild) {
    Write-Step "4/6  Docker 이미지 빌드 (5~10분 소요)"
    Write-Host "  의존성 패키지 설치 중... 진행 상황은 아래 로그를 확인하세요." -ForegroundColor DarkGray
    Write-Host ""

    Push-Location $PSScriptRoot
    try {
        docker compose build --progress=plain
        if ($LASTEXITCODE -ne 0) { Write-Fail "docker compose build 실패" }
    } finally {
        Pop-Location
    }
    Write-OK "이미지 빌드 완료"
} else {
    Write-Warn "4/6  --SkipBuild 플래그 — 빌드 건너뜀"
}

# ── Step 5: mais doctor 실행 ──────────────────────────────────────────────────
Write-Step "5/6  환경 진단 (mais doctor)"
Push-Location $PSScriptRoot
try {
    docker compose run --rm scheduler mais doctor
} catch {
    Write-Warn "mais doctor에서 경고가 있습니다. 위 출력을 확인하세요."
} finally {
    Pop-Location
}

# ── Step 6: 컨테이너 기동 + 브라우저 ─────────────────────────────────────────
if (-not $SkipLaunch) {
    Write-Step "6/6  스케줄러 + 대시보드 기동"
    Push-Location $PSScriptRoot
    try {
        docker compose up -d scheduler dashboard
        if ($LASTEXITCODE -ne 0) { Write-Fail "docker compose up 실패" }
    } finally {
        Pop-Location
    }

    Write-OK "컨테이너 기동 완료"

    Write-Host ""
    Write-Host "  3초 후 브라우저를 엽니다..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 3
    Start-Process "http://localhost:8501"

    Write-Host @"

╔══════════════════════════════════════════════════════════════╗
║  ✓  MAI-System 구축 완료!                                    ║
║                                                              ║
║  대시보드: http://localhost:8501                             ║
║  메트릭:   http://localhost:9100/metrics                     ║
║                                                              ║
║  첫 리서치를 실행하려면:                                     ║
║    대시보드 첫 화면 → "▶ 지금 첫 리서치 실행" 버튼 클릭    ║
║                                                              ║
║  CLI 사용 예:                                                ║
║    docker compose run --rm scheduler mais status            ║
║    docker compose run --rm scheduler mais today             ║
║    docker compose logs -f scheduler                         ║
╚══════════════════════════════════════════════════════════════╝
"@ -ForegroundColor Green
} else {
    Write-Warn "6/6  --SkipLaunch 플래그 — 컨테이너 기동 건너뜀"
    Write-OK "수동 기동: docker compose up -d scheduler dashboard"
}
