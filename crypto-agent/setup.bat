@echo off
rem ======================================================================
rem  setup.bat - minimal bootstrap for crypto-agent on Windows
rem
rem  Single-purpose: create venv, install deps, run tests. No menu, no
rem  key prompting, no multi-line if blocks, no || chains. This file
rem  exists so that if run.bat hits a parser edge case on a particular
rem  Windows build, the user still has a bulletproof fallback.
rem
rem  Usage:  cd into crypto-agent folder, then run:
rem      setup
rem
rem  After this succeeds, use run.bat for the day-to-day flow.
rem ======================================================================

setlocal
cd /d "%~dp0"

echo.
echo [1/4] Checking Python 3.11+
set "PY_CMD="
where py >nul 2>&1
if errorlevel 1 goto :try_plain_python
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3.12"
if defined PY_CMD goto :have_python
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3.11"
if defined PY_CMD goto :have_python
py -3.13 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3.13"

:try_plain_python
if defined PY_CMD goto :have_python
where python >nul 2>&1
if errorlevel 1 goto :no_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto :no_python
set "PY_CMD=python"

:have_python
echo       Using %PY_CMD%
echo.

echo [2/4] Creating virtual environment at .venv
if exist .venv\Scripts\python.exe goto :venv_ok
%PY_CMD% -m venv .venv
if errorlevel 1 goto :venv_fail
:venv_ok
if not exist .venv\Scripts\python.exe goto :venv_fail
echo       venv ready
echo.

echo [3/4] Installing dependencies
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :pip_fail
.venv\Scripts\python.exe -m pip install -e ".[dev]"
if errorlevel 1 goto :pip_fail
echo       Dependencies installed
echo.

echo [4/4] Running test suite
.venv\Scripts\python.exe -m pytest -q
if errorlevel 1 goto :test_fail
echo.
echo ======================================================================
echo  SETUP COMPLETE
echo  Next steps:
echo    run verify   (1 real Anthropic call to confirm your key)
echo    run fetch    (download 2y BTC/ETH/SOL history)
echo    run backtest (heuristic backtest)
echo ======================================================================
endlocal
exit /b 0

:no_python
echo.
echo [X] Python 3.11+ not found on this machine.
echo.
echo Install Python 3.12 from https://www.python.org/downloads/
echo When running the installer, check "Add python.exe to PATH".
echo Then close this window, open a new cmd, cd back here, and run: setup
endlocal
exit /b 1

:venv_fail
echo.
echo [X] Failed to create virtual environment.
echo     Try: rmdir /s /q .venv
echo     Then run: setup
endlocal
exit /b 1

:pip_fail
echo.
echo [X] Dependency install failed. See the pip output above.
endlocal
exit /b 1

:test_fail
echo.
echo [X] Some tests failed. See output above.
endlocal
exit /b 1
