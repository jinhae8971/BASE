#!/usr/bin/env python
"""One-shot Upbit cycle for cron / systemd timers.

    # 매일 09:10 KST 종목 선정 + 진입
    10 9 * * * cd /path/to/repo && python scripts/run_upbit_daily.py scan

    # 5분마다 청산 조건 점검
    */5 * * * * cd /path/to/repo && python scripts/run_upbit_daily.py monitor

Defaults to a dry run: pass ``--execute`` to let ``scan`` actually place orders.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="업비트 일일 사이클 실행")
    parser.add_argument("action", choices=["scan", "monitor", "eod-exit", "snapshot"])
    parser.add_argument(
        "--execute",
        action="store_true",
        help="scan 시 실제 주문 실행 (기본은 시뮬레이션)",
    )
    args = parser.parse_args()

    from common.logging import setup_logging

    setup_logging()

    from upbit.engine import TradingEngine

    engine = TradingEngine()
    if args.action == "scan":
        result = engine.run_selection(dry_run=not args.execute)
    elif args.action == "monitor":
        result = engine.monitor_positions()
    elif args.action == "eod-exit":
        result = engine.monitor_positions(force_exit=True)
    else:
        result = engine.snapshot_equity()

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
