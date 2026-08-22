# ---------------------------------------------------------------------------
# 업비트 자동매매 대시보드 — Windows PowerShell 원클릭 실행
#
#   .\start-upbit.ps1                 기본 (127.0.0.1:8787)
#   .\start-upbit.ps1 -Port 9000      포트 지정
#   .\start-upbit.ps1 -Reinstall      의존성 강제 재설치
#   .\start-upbit.ps1 -NoBrowser      브라우저 자동 실행 안 함
#
# 실행 정책 때문에 막히면 start-upbit.bat 을 더블클릭하거나, 먼저 아래를 실행하세요:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# ---------------------------------------------------------------------------
[CmdletBinding()]
param(
    [int]$Port = 0,
    [switch]$Reinstall,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Say ($m) { Write-Host "`n> $m" -ForegroundColor Cyan }
function Die ($m) { Write-Host "`nX $m" -ForegroundColor Red; exit 1 }

# --- 1. Python -------------------------------------------------------------
function Find-Python {
    # The py launcher is the most reliable way to pin a version on Windows.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @('-3.13', '-3.12', '-3.11')) {
            & py $v -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return [pscustomobject]@{ Exe = 'py'; Args = @($v) }
            }
        }
    }
    foreach ($exe in @('python', 'python3')) {
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            & $exe -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return [pscustomobject]@{ Exe = $exe; Args = @() }
            }
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Die "Python 3.11 이상을 찾지 못했습니다.`n  https://www.python.org/downloads/ 에서 설치하고, 설치 화면에서 'Add python.exe to PATH' 를 체크하세요."
}
$pyArgs = $py.Args
Say "Python: $(& $py.Exe @pyArgs --version 2>&1)"

# --- 2. 가상환경 -----------------------------------------------------------
$venv = Join-Path $PSScriptRoot '.venv'
if (-not (Test-Path $venv)) {
    Say "가상환경을 만듭니다 (.venv)"
    & $py.Exe @pyArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) { Die "가상환경 생성에 실패했습니다." }
    $Reinstall = $true
}

$vpy = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $vpy)) { Die "가상환경이 손상되었습니다. '.venv' 폴더를 지우고 다시 실행하세요." }

# --- 3. 의존성 -------------------------------------------------------------
# Check the package itself, not just third-party imports — a venv can have the
# libraries without `pip install -e .` having been run.
& $vpy -c "import upbit, fastapi, uvicorn, jwt, cryptography, apscheduler" 2>$null
if ($Reinstall -or $LASTEXITCODE -ne 0) {
    Say "의존성을 설치합니다 (처음 한 번은 몇 분 걸립니다)"
    & $vpy -m pip install --upgrade pip | Out-Null
    & $vpy -m pip install -e ".[upbit]"
    if ($LASTEXITCODE -ne 0) { Die "의존성 설치에 실패했습니다. 위 오류를 확인하세요." }
}

if ($Port -gt 0) { $env:UPBIT_DASHBOARD_PORT = "$Port" }

# --- 4. 이미 실행 중이면 브라우저만 열고 끝 --------------------------------
$running = & $vpy -c "from upbit.doctor import dashboard_already_running; print(dashboard_already_running() or '')" 2>$null
if ($running) {
    Say "대시보드가 이미 실행 중입니다 -> $running"
    if (-not $NoBrowser) { Start-Process $running }
    exit 0
}

# --- 5. 사전 점검 ----------------------------------------------------------
Say "실행 전 점검"
& $vpy -m upbit.cli doctor
if ($LASTEXITCODE -ne 0) { Die "점검에 실패했습니다. 위 항목을 해결한 뒤 다시 실행하세요." }

# --- 6. 실행 ---------------------------------------------------------------
$dashHost = & $vpy -c "import os; from common.config import get_setting; print(os.environ.get('UPBIT_DASHBOARD_HOST') or get_setting('upbit.dashboard.host','127.0.0.1'))"
$dashPort = & $vpy -c "import os; from common.config import get_setting; print(os.environ.get('UPBIT_DASHBOARD_PORT') or get_setting('upbit.dashboard.port',8787))"
$url = "http://${dashHost}:${dashPort}"

Say "대시보드를 시작합니다 -> $url   (종료: Ctrl+C)"
if (-not $NoBrowser) {
    # Detached helper so the browser opens once the server is actually listening.
    try {
        Start-Process -WindowStyle Hidden -FilePath 'powershell' -ArgumentList @(
            '-NoProfile', '-Command', "Start-Sleep -Seconds 4; Start-Process '$url'"
        ) | Out-Null
    } catch {
        Write-Host "  (브라우저 자동 실행 실패 — 주소창에 $url 을 직접 입력하세요)" -ForegroundColor DarkYellow
    }
}

& $vpy -m upbit.cli serve --skip-checks
