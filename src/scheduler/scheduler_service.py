"""MAIS automated scheduler — APScheduler-based daily pipeline runner.

Schedules:
- 평일 08:00 KST : research pipeline (agents → consensus → optimizer)
- 평일 09:05 KST : execution pipeline (KIS order placement, if not dry-run)
- 금요일 18:00 KST: weekly reflection agent

Run directly:
    python -m scheduler.scheduler_service
or via Docker:
    command: python -m scheduler.scheduler_service
"""
from __future__ import annotations

import os
import signal
import sys
from datetime import date
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from common.config import get_env, get_setting
from common.logging import get_logger, setup_logging

log = get_logger(__name__)


def _run_research(dry_run: bool) -> None:
    """Research phase — run all specialist agents and build portfolio target."""
    from scheduler.daily_pipeline import run_daily
    try:
        log.info("scheduled.research_start", dry_run=dry_run)
        result = run_daily(as_of=date.today(), dry_run=True)  # research is always dry-run
        log.info("scheduled.research_done", positions=len(result.get("target", {}).get("positions", [])))
    except Exception as exc:
        log.error("scheduled.research_failed", error=str(exc))


def _run_execution(dry_run: bool) -> None:
    """Execution phase — submit KIS orders (live only if dry_run=False)."""
    from scheduler.daily_pipeline import run_daily
    try:
        log.info("scheduled.execution_start", dry_run=dry_run)
        result = run_daily(as_of=date.today(), dry_run=dry_run)
        log.info(
            "scheduled.execution_done",
            n_orders=len(result.get("execution_results", [])),
        )
    except Exception as exc:
        log.error("scheduled.execution_failed", error=str(exc))


def _run_reflection() -> None:
    """Weekly reflection — summarize decisions and write insight report."""
    from agents.reflection_agent import ReflectionAgent
    try:
        log.info("scheduled.reflection_start")
        ReflectionAgent().reflect()
        log.info("scheduled.reflection_done")
    except Exception as exc:
        log.error("scheduled.reflection_failed", error=str(exc))


def build_scheduler() -> BlockingScheduler:
    tz = get_setting("system.timezone", "Asia/Seoul")
    dry_run = os.getenv("DRY_RUN", "true").lower() not in ("false", "0", "no")

    research_time: str = get_setting("scheduler.research_time", "08:00")
    order_time: str = get_setting("scheduler.order_time", "09:05")
    refl_day: str = get_setting("scheduler.reflection_day", "friday")[:3]
    refl_time: str = get_setting("scheduler.reflection_time", "18:00")

    rh, rm = map(int, research_time.split(":"))
    oh, om = map(int, order_time.split(":"))
    rfh, rfm = map(int, refl_time.split(":"))

    scheduler = BlockingScheduler(timezone=tz)

    # Research (all weekdays, research never places orders)
    scheduler.add_job(
        lambda: _run_research(dry_run=True),
        CronTrigger(day_of_week="mon-fri", hour=rh, minute=rm, timezone=tz),
        id="daily_research",
        max_instances=1,
        coalesce=True,
    )

    # Order placement (all weekdays)
    scheduler.add_job(
        lambda: _run_execution(dry_run=dry_run),
        CronTrigger(day_of_week="mon-fri", hour=oh, minute=om, timezone=tz),
        id="daily_execution",
        max_instances=1,
        coalesce=True,
    )

    # Weekly reflection
    scheduler.add_job(
        _run_reflection,
        CronTrigger(day_of_week=refl_day, hour=rfh, minute=rfm, timezone=tz),
        id="weekly_reflection",
        max_instances=1,
        coalesce=True,
    )

    return scheduler


def main() -> None:
    setup_logging()
    env = get_env()
    dry_run = os.getenv("DRY_RUN", "true").lower() not in ("false", "0", "no")

    log.info(
        "mais.scheduler_starting",
        kis_env=env.kis_env,
        dry_run=dry_run,
        timezone=get_setting("system.timezone", "Asia/Seoul"),
    )

    scheduler = build_scheduler()

    def _shutdown(sig: int, frame: Any) -> None:
        log.info("mais.scheduler_shutdown", signal=sig)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("mais.scheduler_stopped")


if __name__ == "__main__":
    main()
