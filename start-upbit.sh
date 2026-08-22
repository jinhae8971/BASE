#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 업비트 자동매매 대시보드 — macOS / Linux 원클릭 실행
#
#   ./start-upbit.sh              기본 (127.0.0.1:8787)
#   ./start-upbit.sh --port 9000  포트 지정
#   ./start-upbit.sh --reinstall  의존성 강제 재설치
#
# 가상환경(.venv)이 없으면 만들고, 의존성을 설치하고, 사전 점검을 돌린 뒤
# 대시보드를 띄우고 브라우저를 엽니다.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
PORT=""
REINSTALL=0
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --reinstall) REINSTALL=1; shift ;;
    --help|-h)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$1"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# --- 1. Python -------------------------------------------------------------
PY=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PY="$candidate"; break
    fi
  fi
done
[[ -n "$PY" ]] || die "Python 3.11 이상을 찾지 못했습니다. https://www.python.org/downloads/ 에서 설치하세요."
say "Python: $($PY --version)"

# --- 2. 가상환경 -----------------------------------------------------------
if [[ ! -d "$VENV" ]]; then
  say "가상환경을 만듭니다 ($VENV)"
  "$PY" -m venv "$VENV"
  REINSTALL=1
fi
VPY="$VENV/bin/python"
[[ -x "$VPY" ]] || die "가상환경이 손상되었습니다. '$VENV' 를 지우고 다시 실행하세요."

# --- 3. 의존성 -------------------------------------------------------------
# Check the package itself, not just third-party imports — a venv can have the
# libraries without `pip install -e .` having been run.
if [[ $REINSTALL -eq 1 ]] || ! "$VPY" -c 'import upbit, fastapi, uvicorn, jwt, cryptography, apscheduler' 2>/dev/null; then
  say "의존성을 설치합니다 (처음 한 번은 몇 분 걸립니다)"
  "$VPY" -m pip install --upgrade pip >/dev/null
  "$VPY" -m pip install -e ".[upbit]" || die "의존성 설치에 실패했습니다. 위 오류를 확인하세요."
fi

if [[ -n "$PORT" ]]; then export UPBIT_DASHBOARD_PORT="$PORT"; fi

# --- 4. 이미 실행 중이면 브라우저만 열고 끝 --------------------------------
RUNNING_URL="$("$VPY" -c 'from upbit.doctor import dashboard_already_running; print(dashboard_already_running() or "")' 2>/dev/null || true)"
if [[ -n "$RUNNING_URL" ]]; then
  say "대시보드가 이미 실행 중입니다 → $RUNNING_URL"
  if command -v open >/dev/null 2>&1; then open "$RUNNING_URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$RUNNING_URL" >/dev/null 2>&1
  fi
  exit 0
fi

# --- 5. 사전 점검 ----------------------------------------------------------
say "실행 전 점검"
if ! "$VPY" -m upbit.cli doctor; then
  die "점검에 실패했습니다. 위 항목을 해결한 뒤 다시 실행하세요."
fi

# --- 6. 실행 ---------------------------------------------------------------
HOST="$("$VPY" -c 'from common.config import get_setting; import os; print(os.environ.get("UPBIT_DASHBOARD_HOST") or get_setting("upbit.dashboard.host","127.0.0.1"))')"
PORT="$("$VPY" -c 'from common.config import get_setting; import os; print(os.environ.get("UPBIT_DASHBOARD_PORT") or get_setting("upbit.dashboard.port",8787))')"
URL="http://${HOST}:${PORT}"

say "대시보드를 시작합니다 → $URL   (종료: Ctrl+C)"
(
  sleep 3
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1
  fi
) &

exec "$VPY" -m upbit.cli serve --skip-checks "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
