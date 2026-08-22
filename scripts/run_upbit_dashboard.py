#!/usr/bin/env python
"""Launch the Upbit dashboard (FastAPI + scheduler) on localhost.

    python scripts/run_upbit_dashboard.py
    python scripts/run_upbit_dashboard.py --host 0.0.0.0 --port 9000

Binding to anything other than 127.0.0.1 exposes your account controls to the
network — set ``UPBIT_DASHBOARD_TOKEN`` first if you do that.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="업비트 자동매매 대시보드 실행")
    parser.add_argument("--host", default=None, help="바인드 주소 (기본 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="포트 (기본 8787)")
    args = parser.parse_args()

    if args.host:
        os.environ["UPBIT_DASHBOARD_HOST"] = args.host
    if args.port:
        os.environ["UPBIT_DASHBOARD_PORT"] = str(args.port)

    exposed = args.host and args.host not in ("127.0.0.1", "localhost")
    if exposed and not os.environ.get("UPBIT_DASHBOARD_TOKEN"):
        print(
            "[경고] 외부 주소에 바인드하면서 UPBIT_DASHBOARD_TOKEN 이 설정되지 않았습니다.\n"
            "       누구나 API 키 설정과 주문 실행에 접근할 수 있습니다.",
            file=sys.stderr,
        )

    from dashboard.server import main as serve

    serve()


if __name__ == "__main__":
    main()
