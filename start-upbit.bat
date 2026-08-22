@echo off
REM ---------------------------------------------------------------------------
REM 업비트 자동매매 대시보드 — 더블클릭 실행용 (Windows)
REM PowerShell 실행 정책을 이 프로세스에만 우회해서 start-upbit.ps1 을 돌립니다.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-upbit.ps1" %*
if errorlevel 1 (
  echo.
  echo 실행에 실패했습니다. 위 메시지를 확인하세요.
  pause
)
endlocal
