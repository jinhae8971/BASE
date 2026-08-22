#!/bin/sh
# ---------------------------------------------------------------------------
# 컨테이너 진입점.
#   serve (기본)  → 사전 점검 후 대시보드 + 스케줄러 실행
#   그 외          → mais-upbit 하위 명령으로 전달 (scan / status / holdings ...)
# ---------------------------------------------------------------------------
set -e

DATA_DIR="${UPBIT_DATA_DIR:-/app/data_store}"

if ! mkdir -p "$DATA_DIR" 2>/dev/null || ! touch "$DATA_DIR/.write_probe" 2>/dev/null; then
    cat >&2 <<MSG

[중단] $DATA_DIR 에 쓸 수 없습니다.
       거래 기록과 암호화된 API 키가 저장되는 곳이라 반드시 쓰기 가능해야 합니다.

       바인드 마운트를 쓰신다면 호스트 디렉터리 소유자를 맞추세요:
         sudo chown -R 10001:10001 ./data_store
       또는 compose 의 기본값인 named volume(upbit-data)을 그대로 쓰세요.

MSG
    exit 1
fi
rm -f "$DATA_DIR/.write_probe"

if [ "$1" = "serve" ]; then
    # `serve` runs the preflight itself and blocks only on failures that make the
    # app unusable — a rejected API key must not stop the dashboard from coming
    # up, because that is where you fix it.
    if [ "${UPBIT_SKIP_DOCTOR:-0}" = "1" ]; then
        exec mais-upbit serve --skip-checks
    fi
    exec mais-upbit serve
fi

exec mais-upbit "$@"
