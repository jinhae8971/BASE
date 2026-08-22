#!/usr/bin/env python
"""Emergency liquidation for the Upbit engine.

    python scripts/upbit_kill_switch.py --confirm I-UNDERSTAND

Flattens every position the engine opened. Coins registered as 장기보유 are
untouched — the guard subtracts their locked quantity before any sell is sized.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="업비트 자동매매 전량 청산")
    parser.add_argument("--confirm", required=True, help="'I-UNDERSTAND' 를 입력하세요.")
    parser.add_argument("--stop-scheduler", action="store_true", help="스케줄도 함께 비활성화")
    args = parser.parse_args()

    if args.confirm != "I-UNDERSTAND":
        print("확인 문구가 올바르지 않습니다: --confirm I-UNDERSTAND", file=sys.stderr)
        raise SystemExit(1)

    from common.logging import setup_logging

    setup_logging()

    from upbit.engine import TradingEngine
    from upbit.strategy import save_config

    result = TradingEngine().liquidate_all(reason="panic")
    if args.stop_scheduler:
        save_config({"schedule": {"enabled": False}})
        result["scheduler_disabled"] = True

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
