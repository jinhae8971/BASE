@echo off
rem ======================================================================
rem  run.bat - one-shot launcher for crypto-agent on Windows
rem
rem  Flat goto control flow only. No multi-line parenthesized if blocks,
rem  no || chains, no ... trailing tokens. Keeps cmd.exe's parser happy
rem  across codepages and Windows builds.
rem
rem  Usage:
rem      run                (interactive menu)
rem      run setup          (first-time bootstrap; delegates to setup.bat)
rem      run test           (pytest)
rem      run verify         (one real Anthropic call)
rem      run fetch          (download 2y BTC/ETH/SOL klines)
rem      run backtest       (heuristic backtest)
rem      run backtest-llm   (real LLM backtest, capped 60 days)
rem      run paper          (one paper-trade cycle)
rem      run schedule       (paper scheduler)
rem      run dashboard      (generate + open HTML dashboard)
rem      run halt           (create HALT file)
rem      run resume         (remove HALT file)
rem      run help           (this message)
rem ======================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

rem Force Python to emit UTF-8 on stdout regardless of the console's OEM
rem codepage. Without this, print() with any non-ASCII char crashes with
rem UnicodeEncodeError on Korean Windows (cp949 default).
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "VENV_DIR=.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

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
goto :cmd_help

rem ======================================================================
rem  :cmd_setup - delegate to setup.bat which has a bulletproof flow
rem ======================================================================
:cmd_setup
call setup.bat
exit /b %errorlevel%

rem ======================================================================
rem  Shared prerequisite: make sure deps are installed before running
rem  anything that imports src/. Uses ONE call to setup.bat if missing.
rem ======================================================================
:ensure_ready
if not exist "%VENV_PY%" goto :ensure_ready_bootstrap
"%VENV_PY%" -c "import src, pytest, httpx, anthropic" >nul 2>&1
if errorlevel 1 goto :ensure_ready_bootstrap
call :load_env_file
exit /b 0
:ensure_ready_bootstrap
echo [*] First run - bootstrapping via setup.bat ...
call setup.bat
if errorlevel 1 exit /b 1
call :load_env_file
exit /b 0

rem ======================================================================
rem  .env loader - set each KEY=VALUE pair that isn't already set
rem ======================================================================
:load_env_file
if not exist .env exit /b 0
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do call :load_env_line "%%A" "%%B"
exit /b 0
:load_env_line
set "k=%~1"
set "v=%~2"
if "%k%"=="" exit /b 0
set "v=%v:"=%"
if defined %k% exit /b 0
set "%k%=%v%"
exit /b 0

rem ======================================================================
rem  Secret prompt helper - PowerShell hidden input
rem ======================================================================
:prompt_secret
set "VNAME=%~1"
set "LABEL=%~2"
if defined %VNAME% exit /b 0
echo.
echo === %LABEL% needed ===
echo   Held in this window only. NOT saved to disk.
echo   Paste the value and press Enter (input hidden):
for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "$s = Read-Host -AsSecureString; [System.Runtime.InteropServices.Marshal]::PtrToStringAuto([System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))"`) do set "%VNAME%=%%V"
if not defined %VNAME% goto :prompt_empty
echo [v] %VNAME% set for this session only.
exit /b 0
:prompt_empty
echo [X] Empty input. Aborting.
exit /b 1

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
rem  Commands (one-line bodies where possible)
rem ======================================================================

:cmd_test
call :ensure_ready
if errorlevel 1 exit /b 1
"%VENV_PY%" -m pytest -q
exit /b %errorlevel%

:cmd_verify
call :ensure_ready
if errorlevel 1 exit /b 1
call :require_anthropic
if errorlevel 1 exit /b 1
echo [*] Running verify_anthropic.py (6 real Anthropic calls, ~$0.15)
"%VENV_PY%" -m scripts.verify_anthropic
exit /b %errorlevel%

:cmd_fetch
call :ensure_ready
if errorlevel 1 exit /b 1
echo [*] Downloading 2-year daily klines for BTCUSDT, ETHUSDT, SOLUSDT
"%VENV_PY%" -m scripts.fetch_history --symbols BTCUSDT,ETHUSDT,SOLUSDT --start 2023-01-01 --end 2025-01-01 --interval 1d
exit /b %errorlevel%

:cmd_backtest
call :ensure_ready
if errorlevel 1 exit /b 1
if not exist "data\history\1d\BTCUSDT.ndjson" goto :backtest_synth
echo [*] Running heuristic backtest on archived Binance history
"%VENV_PY%" -m scripts.run_backtest --from-archive --symbols BTCUSDT,ETHUSDT,SOLUSDT
exit /b %errorlevel%
:backtest_synth
echo [!] No archive found - using synthetic drift. Run "run fetch" first.
"%VENV_PY%" -m scripts.run_backtest --days 180
exit /b %errorlevel%

:cmd_backtest_llm
call :ensure_ready
if errorlevel 1 exit /b 1
call :require_anthropic
if errorlevel 1 exit /b 1
if not exist "data\history\1d\BTCUSDT.ndjson" goto :backtest_llm_no_archive
echo [*] REAL LLM backtest - uses Anthropic credits (default: 60 days, ~$9)
"%VENV_PY%" -m scripts.run_backtest --from-archive --symbols BTCUSDT,ETHUSDT,SOLUSDT --real-llm
exit /b %errorlevel%
:backtest_llm_no_archive
echo [X] No archive found. Run "run fetch" first.
exit /b 1

:cmd_paper_once
call :ensure_ready
if errorlevel 1 exit /b 1
call :require_anthropic
if errorlevel 1 exit /b 1
call :require_binance_testnet
if errorlevel 1 exit /b 1
echo [*] Running one paper-trade cycle on Binance testnet
"%VENV_PY%" -m src.orchestrator.run_daily once
exit /b %errorlevel%

:cmd_paper_schedule
call :ensure_ready
if errorlevel 1 exit /b 1
call :require_anthropic
if errorlevel 1 exit /b 1
call :require_binance_testnet
if errorlevel 1 exit /b 1
echo [!] Scheduler runs until you press Ctrl+C or create a HALT file.
echo [*] Starting paper-trade scheduler on 24-hour interval
"%VENV_PY%" -m src.orchestrator.run_daily schedule --interval-hours 24
exit /b %errorlevel%

:cmd_dashboard
call :ensure_ready
if errorlevel 1 exit /b 1
echo [*] Generating dashboard from data\runs
"%VENV_PY%" -m src.dashboard.generator
if errorlevel 1 exit /b 1
set "DASH=%cd%\data\dashboard\index.html"
if not exist "%DASH%" goto :dashboard_missing
echo [v] Dashboard: %DASH%
start "" "%DASH%"
exit /b 0
:dashboard_missing
echo [X] Dashboard generator did not produce %DASH%
exit /b 1

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
echo   run backtest       Heuristic baseline (free, quick)
echo   run backtest-llm   Real Anthropic backtest (60 days, paid)
echo.
echo Paper trading (Binance testnet):
echo   run paper          One daily cycle
echo   run schedule       Scheduler until Ctrl+C
echo.
echo Operations:
echo   run dashboard      Generate + open HTML dashboard
echo   run halt           Stop the running scheduler
echo   run resume         Remove HALT file
echo   run menu           Interactive menu (default)
echo   run help           This message
echo.
echo Keys (ANTHROPIC_API_KEY, BINANCE_API_KEY, BINANCE_API_SECRET) are
echo prompted only when needed and held in THIS window only. They are
echo NEVER written to disk.
echo.
exit /b 0

rem ======================================================================
rem  Interactive menu (also the default when invoked with no args)
rem ======================================================================
:menu_loop
call :ensure_ready
if errorlevel 1 goto :error_exit
:menu_print
echo.
echo ============================================
echo   crypto-agent launcher
echo ============================================
echo   1^) Run tests
echo   2^) Verify Anthropic key
echo   3^) Fetch Binance history (2y)
echo   4^) Backtest - heuristic (free)
echo   5^) Backtest - real LLM (paid)
echo   6^) Paper trade - one cycle
echo   7^) Paper trade - scheduler
echo   8^) Generate + open dashboard
echo   9^) HALT scheduler
echo  10^) Resume (remove HALT)
echo   h^) Help
echo   q^) Quit
echo.
set "choice="
set /p "choice=Choose: "
if "%choice%"=="" goto :menu_loop
if "%choice%"=="1"   call :cmd_test
if "%choice%"=="2"   call :cmd_verify
if "%choice%"=="3"   call :cmd_fetch
if "%choice%"=="4"   call :cmd_backtest
if "%choice%"=="5"   call :cmd_backtest_llm
if "%choice%"=="6"   call :cmd_paper_once
if "%choice%"=="7"   call :cmd_paper_schedule
if "%choice%"=="8"   call :cmd_dashboard
if "%choice%"=="9"   call :cmd_halt
if "%choice%"=="10"  call :cmd_resume
if /i "%choice%"=="h" call :cmd_help
if /i "%choice%"=="q" goto :clean_exit
goto :menu_loop

:clean_exit
echo Bye.
endlocal
exit /b 0

:error_exit
echo.
echo [X] Aborted due to an earlier error.
endlocal
exit /b 1
