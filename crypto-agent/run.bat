@echo off
rem ======================================================================
rem  run.bat - one-shot launcher for crypto-agent on Windows
rem
rem  Handles venv bootstrap, dependency install, API-key prompting, and
rem  every common operator task behind a single menu or subcommand.
rem
rem  Keys (ANTHROPIC_API_KEY, BINANCE_API_KEY, BINANCE_API_SECRET) are
rem  prompted interactively in THIS command window only and are NEVER
rem  written to disk. Closing the window clears every secret.
rem
rem  NOTE: this file must be stored with CRLF line endings (enforced by
rem  .gitattributes). LF endings cause cmd.exe to silently mis-parse
rem  multi-line if blocks.
rem
rem  Usage:
rem      run                (interactive menu - you can also double-click)
rem      run setup          (create venv, install deps, run tests)
rem      run verify         (one real Anthropic call to check your key)
rem      run fetch          (download 2y BTC/ETH/SOL klines)
rem      run backtest       (heuristic baseline backtest)
rem      run backtest-llm   (real Anthropic backtest, ~$9, capped 60 days)
rem      run paper          (one paper-trade cycle on Binance testnet)
rem      run schedule       (paper-trade scheduler, Ctrl+C to stop)
rem      run dashboard      (generate + open HTML dashboard)
rem      run halt           (create HALT file)
rem      run resume         (remove HALT file)
rem      run help           (this message)
rem ======================================================================

setlocal enabledelayedexpansion
rem Always run from this script's directory, regardless of where invoked.
cd /d "%~dp0"

set "VENV_DIR=.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_ACT=%VENV_DIR%\Scripts\activate.bat"

rem ----------------------------------------------------------------------
rem  Dispatch on the first argument
rem ----------------------------------------------------------------------

set "CMD=%~1"
if "%CMD%"=="" set "CMD=menu"

if /i "%CMD%"=="setup"         goto :cmd_setup
if /i "%CMD%"=="test"          goto :cmd_test
if /i "%CMD%"=="verify"        goto :cmd_verify
if /i "%CMD%"=="fetch"         goto :cmd_fetch
if /i "%CMD%"=="backtest"      goto :cmd_backtest
if /i "%CMD%"=="backtest-llm"  goto :cmd_backtest_llm
if /i "%CMD%"=="paper"         goto :cmd_paper_once
if /i "%CMD%"=="schedule"      goto :cmd_paper_schedule
if /i "%CMD%"=="dashboard"     goto :cmd_dashboard
if /i "%CMD%"=="halt"          goto :cmd_halt
if /i "%CMD%"=="resume"        goto :cmd_resume
if /i "%CMD%"=="menu"          goto :menu_loop
if /i "%CMD%"=="help"          goto :cmd_help
if /i "%CMD%"=="-h"            goto :cmd_help
if /i "%CMD%"=="/?"            goto :cmd_help

echo [X] Unknown command: %CMD%
echo.
goto :cmd_help

rem ======================================================================
rem  Interactive menu
rem ======================================================================
:menu_loop
call :ensure_deps || goto :error_exit
echo.
echo ============================================
echo   crypto-agent launcher
echo ============================================
echo   1^) Setup / reinstall
echo   2^) Run tests
echo   3^) Verify Anthropic key
echo   4^) Fetch Binance history (2y)
echo   5^) Backtest - heuristic (free)
echo   6^) Backtest - real LLM (paid)
echo   7^) Paper trade - one cycle
echo   8^) Paper trade - scheduler
echo   9^) Generate + open dashboard
echo  10^) HALT scheduler
echo  11^) Resume (remove HALT)
echo   h^) Help
echo   q^) Quit
echo.
set "choice="
set /p "choice=Choose: "
if "%choice%"=="" goto :menu_loop
if "%choice%"=="1"   ( call :cmd_setup         & goto :menu_loop )
if "%choice%"=="2"   ( call :cmd_test          & goto :menu_loop )
if "%choice%"=="3"   ( call :cmd_verify        & goto :menu_loop )
if "%choice%"=="4"   ( call :cmd_fetch         & goto :menu_loop )
if "%choice%"=="5"   ( call :cmd_backtest      & goto :menu_loop )
if "%choice%"=="6"   ( call :cmd_backtest_llm  & goto :menu_loop )
if "%choice%"=="7"   ( call :cmd_paper_once    & goto :menu_loop )
if "%choice%"=="8"   ( call :cmd_paper_schedule & goto :menu_loop )
if "%choice%"=="9"   ( call :cmd_dashboard     & goto :menu_loop )
if "%choice%"=="10"  ( call :cmd_halt          & goto :menu_loop )
if "%choice%"=="11"  ( call :cmd_resume        & goto :menu_loop )
if /i "%choice%"=="h" ( call :cmd_help         & goto :menu_loop )
if /i "%choice%"=="q" goto :clean_exit
echo [!] Unknown choice: %choice%
goto :menu_loop

rem ======================================================================
rem  Helpers: Python + venv + dependencies
rem ======================================================================

:find_python
rem Prefer py launcher with 3.12, then 3.11, then generic python.
set "PYTHON_EXE="
where py >nul 2>&1
if not errorlevel 1 (
    for %%V in (3.12 3.11 3.13) do (
        py -%%V -c "import sys" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_EXE=py -%%V"
            goto :find_python_done
        )
    )
)
where python >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%A in ('python -c "import sys;print(sys.version_info.major*100+sys.version_info.minor)" 2^>nul') do set "PYV=%%A"
    if defined PYV if !PYV! GEQ 311 set "PYTHON_EXE=python"
)
:find_python_done
if defined PYTHON_EXE exit /b 0
exit /b 1

:install_python
rem Attempts to download + silent install Python 3.12 (per-user, no admin).
rem On success, adds the new Python to PATH in the current session and
rem returns 0 so the caller can continue setup without a restart.
echo.
echo [!] Python 3.11+ not found on this machine.
echo.
echo This launcher can download and install Python 3.12 for you automatically.
echo The installer is downloaded to %%TEMP%%, runs in silent per-user mode
echo (no admin rights required), then the launcher continues setup in the
echo same window.
echo.
choice /c YN /n /m "Install Python 3.12 now? (Y/N) "
if errorlevel 2 (
    echo.
    echo Manual install:
    echo   1^) Open https://www.python.org/downloads/
    echo   2^) Click "Download Python 3.12.x"
    echo   3^) Run the installer - CHECK "Add python.exe to PATH"
    echo   4^) Close this window, open a new cmd
    echo   5^) cd %%USERPROFILE%%\Desktop\BASE\crypto-agent
    echo   6^) run setup
    exit /b 1
)

set "PY_URL=https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
set "PY_INSTALLER=%TEMP%\python-3.12.8-amd64.exe"

echo.
echo [*] Downloading Python 3.12.8 (~25 MB) from python.org ...
rem Prefer curl.exe (built into Windows 10 1803+) since it avoids
rem PowerShell quoting pitfalls. Fall back to PowerShell if curl is
rem missing or the download fails.
set "DOWNLOAD_OK=0"
where curl >nul 2>&1
if not errorlevel 1 (
    curl.exe -L --fail --silent --show-error -o "%PY_INSTALLER%" "%PY_URL%"
    if not errorlevel 1 set "DOWNLOAD_OK=1"
)
if "%DOWNLOAD_OK%"=="0" (
    echo [*] curl unavailable or failed. Trying PowerShell ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri $env:PY_URL -OutFile $env:PY_INSTALLER -UseBasicParsing"
    if not errorlevel 1 if exist "%PY_INSTALLER%" set "DOWNLOAD_OK=1"
)
if "%DOWNLOAD_OK%"=="0" (
    echo [X] Download failed. Check your internet connection or install manually
    echo     from https://www.python.org/downloads/
    exit /b 1
)

echo [*] Running silent installer (per-user, adds to PATH). This takes ~1 minute...
"%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
set "INSTALL_RC=%errorlevel%"
del /q "%PY_INSTALLER%" >nul 2>&1
if not "%INSTALL_RC%"=="0" (
    echo [X] Python installer exited with code %INSTALL_RC%
    exit /b 1
)

echo [v] Python 3.12 installed successfully.
echo [*] Adding Python 3.12 to PATH for this session ...

rem Per-user install location. Try the standard path first, then the
rem py-launcher-aware fallback.
set "PY312_ROOT=%LOCALAPPDATA%\Programs\Python\Python312"
if not exist "%PY312_ROOT%\python.exe" (
    rem Some installer versions drop it under a slightly different name.
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python312*") do (
        if exist "%%D\python.exe" set "PY312_ROOT=%%D"
    )
)
if not exist "%PY312_ROOT%\python.exe" (
    echo [!] Python installed but not found at expected path:
    echo     %PY312_ROOT%
    echo     Close this window, open a NEW cmd, and run: run setup
    exit /b 2
)

set "PATH=%PY312_ROOT%;%PY312_ROOT%\Scripts;%PATH%"
echo [v] PATH updated. Python 3.12 is now available in this window.

rem Re-run find_python now that PATH contains the new Python.
call :find_python
if errorlevel 1 (
    echo [X] Python still not detected after install. This should not happen.
    echo     Try closing and reopening cmd, then running: run setup
    exit /b 1
)
echo [v] Continuing with setup ...
exit /b 0

:ensure_venv
if exist "%VENV_PY%" exit /b 0
call :find_python
if errorlevel 1 (
    call :install_python
    exit /b %errorlevel%
)
echo [*] Creating virtual environment in %VENV_DIR% ...
echo [v] Using %PYTHON_EXE%
%PYTHON_EXE% -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo [X] Failed to create venv.
    exit /b 1
)
echo [v] venv created.
exit /b 0

:ensure_deps
call :ensure_venv || exit /b 1
if not exist "%VENV_PY%" (
    echo [X] venv python.exe not found at %VENV_PY%
    echo     This means venv creation silently failed. Try:
    echo       rmdir /s /q %VENV_DIR%
    echo       run setup
    exit /b 1
)
"%VENV_PY%" -c "import src, pytest, httpx, anthropic" >nul 2>&1
if errorlevel 1 (
    echo [*] Installing dependencies (takes ~30-60s on first run) ...
    "%VENV_PY%" -m pip install --upgrade pip
    if errorlevel 1 (
        echo [X] pip upgrade failed.
        exit /b 1
    )
    "%VENV_PY%" -m pip install -e ".[dev]"
    if errorlevel 1 (
        echo [X] Dependency install failed. See the pip output above.
        exit /b 1
    )
    echo [v] Dependencies installed.
)
call :load_env_file
exit /b 0

rem ----------------------------------------------------------------------
rem  .env loader - parse KEY=VALUE pairs, skip comments / blanks, do not
rem  clobber values already present in the environment.
rem ----------------------------------------------------------------------
:load_env_file
if not exist .env exit /b 0
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" (
        set "k=%%A"
        set "v=%%B"
        rem strip surrounding quotes
        if defined v (
            set "v=!v:"=!"
        )
        rem only set if empty
        if not defined !k! set "!k!=!v!"
    )
)
exit /b 0

rem ----------------------------------------------------------------------
rem  Interactive secret prompt - uses PowerShell for hidden input so the
rem  key never echoes to screen or scrolls into history.
rem ----------------------------------------------------------------------
:prompt_secret
rem %1 = variable name, %2 = friendly label (quote it if it has spaces)
set "VNAME=%~1"
set "LABEL=%~2"

if defined !VNAME! exit /b 0

echo.
echo === !LABEL! needed ===
echo   Held in this window only. NOT saved to disk.
echo   Paste the value and press Enter (input is hidden):
for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "$s = Read-Host -AsSecureString; [System.Runtime.InteropServices.Marshal]::PtrToStringAuto([System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))"`) do set "%VNAME%=%%V"

if not defined !VNAME! (
    echo [X] Empty input. Aborting.
    exit /b 1
)
echo [v] !VNAME! set for this session only.
exit /b 0

:require_anthropic
call :prompt_secret ANTHROPIC_API_KEY "Anthropic API key"
exit /b %errorlevel%

:require_binance_testnet
call :prompt_secret BINANCE_API_KEY "Binance testnet API key"
if errorlevel 1 exit /b 1
call :prompt_secret BINANCE_API_SECRET "Binance testnet API secret"
if errorlevel 1 exit /b 1
set "BINANCE_TESTNET=true"
set "TRADING_MODE=paper"
exit /b 0

rem ======================================================================
rem  Commands
rem ======================================================================

:cmd_setup
call :ensure_deps || exit /b 1
echo [*] Running test suite to verify installation ...
"%VENV_PY%" -m pytest -q
if errorlevel 1 (
    echo [X] Some tests failed. See output above.
    exit /b 1
)
echo [v] Setup complete - all tests passing.
exit /b 0

:cmd_test
call :ensure_deps || exit /b 1
"%VENV_PY%" -m pytest -q
exit /b %errorlevel%

:cmd_verify
call :ensure_deps || exit /b 1
call :require_anthropic || exit /b 1
echo [*] Running verify_anthropic.py (6 real Anthropic calls, ~$0.15)
"%VENV_PY%" -m scripts.verify_anthropic
exit /b %errorlevel%

:cmd_fetch
call :ensure_deps || exit /b 1
echo [*] Downloading 2-year daily klines for BTCUSDT, ETHUSDT, SOLUSDT ...
"%VENV_PY%" -m scripts.fetch_history --symbols BTCUSDT,ETHUSDT,SOLUSDT --start 2023-01-01 --end 2025-01-01 --interval 1d
exit /b %errorlevel%

:cmd_backtest
call :ensure_deps || exit /b 1
if not exist "data\history\1d\BTCUSDT.ndjson" (
    echo [!] No archive found - running with synthetic drift instead.
    echo [!] Run "run fetch" first to use real Binance history.
    "%VENV_PY%" -m scripts.run_backtest --days 180
    exit /b %errorlevel%
)
echo [*] Running heuristic backtest on archived Binance history ...
"%VENV_PY%" -m scripts.run_backtest --from-archive --symbols BTCUSDT,ETHUSDT,SOLUSDT
exit /b %errorlevel%

:cmd_backtest_llm
call :ensure_deps || exit /b 1
call :require_anthropic || exit /b 1
if not exist "data\history\1d\BTCUSDT.ndjson" (
    echo [X] No archive found. Run "run fetch" first.
    exit /b 1
)
echo [*] REAL LLM backtest - uses Anthropic credits (~$9 capped, 60 days)
"%VENV_PY%" -m scripts.run_backtest --from-archive --symbols BTCUSDT,ETHUSDT,SOLUSDT --real-llm
exit /b %errorlevel%

:cmd_paper_once
call :ensure_deps || exit /b 1
call :require_anthropic || exit /b 1
call :require_binance_testnet || exit /b 1
echo [*] Running one paper-trade cycle on Binance testnet ...
"%VENV_PY%" -m src.orchestrator.run_daily once
exit /b %errorlevel%

:cmd_paper_schedule
call :ensure_deps || exit /b 1
call :require_anthropic || exit /b 1
call :require_binance_testnet || exit /b 1
echo [!] Scheduler runs until you press Ctrl+C or create a HALT file.
echo [*] Starting paper-trade scheduler on 24-hour interval ...
"%VENV_PY%" -m src.orchestrator.run_daily schedule --interval-hours 24
exit /b %errorlevel%

:cmd_dashboard
call :ensure_deps || exit /b 1
echo [*] Generating dashboard from data\runs\ ...
"%VENV_PY%" -m src.dashboard.generator
if errorlevel 1 exit /b 1
set "DASH=%cd%\data\dashboard\index.html"
if not exist "%DASH%" (
    echo [X] Dashboard generator did not produce %DASH%
    exit /b 1
)
echo [v] Dashboard: %DASH%
start "" "%DASH%"
exit /b 0

:cmd_halt
type NUL > HALT
echo [v] HALT file created. The scheduler will stop on its next tick.
exit /b 0

:cmd_resume
if exist HALT del /q HALT
echo [v] HALT file removed.
exit /b 0

:cmd_help
echo.
echo crypto-agent launcher
echo.
echo Usage: run [command]
echo.
echo First-time setup:
echo   run setup          Create venv, install deps, run tests
echo.
echo Verification:
echo   run test           Run the test suite
echo   run verify         One real Anthropic call with your key
echo   run fetch          Download 2y BTC/ETH/SOL daily klines
echo.
echo Backtesting:
echo   run backtest       Heuristic baseline (free, ~5 sec)
echo   run backtest-llm   Real Anthropic backtest (~$9, 5-10 min)
echo.
echo Paper trading (Binance testnet):
echo   run paper          One daily cycle
echo   run schedule       Scheduler until Ctrl+C
echo.
echo Operations:
echo   run dashboard      Generate + open HTML dashboard
echo   run halt           Stop the running scheduler (creates HALT file)
echo   run resume         Remove HALT file
echo   run menu           Interactive menu (this is the default)
echo   run help           This message
echo.
echo Keys (ANTHROPIC_API_KEY, BINANCE_API_KEY, BINANCE_API_SECRET) are
echo prompted interactively only when needed and are held in THIS window
echo only. They are NEVER written to disk.
echo.
echo If a .env file exists at the repo root, keys found there are loaded
echo automatically. To unset, remove them from .env or unset the
echo corresponding env var in your shell.
echo.
exit /b 0

rem ======================================================================
rem  Exit paths
rem ======================================================================

:clean_exit
echo Bye.
endlocal
exit /b 0

:error_exit
echo.
echo [X] Aborted due to an earlier error.
endlocal
exit /b 1
