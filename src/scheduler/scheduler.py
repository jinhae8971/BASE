"""APScheduler-based long-running scheduler.

Runs in the foreground (sleeps until next trigger) so it plays nicely with
``docker run`` / ``docker compose up``. All trigger times come from
``config/settings.yaml::scheduler``.

Triggers:
    research_time   — daily at 08:00 KST (research + decision)
    order_time      — daily at 09:05 KST (execution; broker orders)
    eod_review_time — daily at 16:00 KST (mark-to-market + outcome backfill)
    reflection      — every Friday at 18:00 KST (reflection report)
"""
from __future__ import annotations

import signal
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone

from common.config import get_setting
from common.logging import get_logger, setup_logging

log = get_logger(__name__)


def _hhmm(value: str, default: str) -> tuple[int, int]:
    s = (value or default).strip()
    h, m = s.split(":")
    return int(h), int(m)


def build_scheduler() -> BlockingScheduler:
    setup_logging()
    tz_name = str(get_setting("system.timezone", "Asia/Seoul"))
    tz = timezone(tz_name)
    sched = BlockingScheduler(timezone=tz)

    research_h, research_m = _hhmm(str(get_setting("scheduler.research_time", "08:00")), "08:00")
    order_h, order_m = _hhmm(str(get_setting("scheduler.order_time", "09:05")), "09:05")
    eod_h, eod_m = _hhmm(str(get_setting("scheduler.eod_review_time", "16:00")), "16:00")
    refl_day = str(get_setting("scheduler.reflection_day", "fri"))[:3].lower()
    refl_h, refl_m = _hhmm(str(get_setting("scheduler.reflection_time", "18:00")), "18:00")

    def _research_job() -> None:
        from scheduler.daily_pipeline import run_daily

        log.info("scheduler.research.fire")
        try:
            run_daily(dry_run=True)  # research only — no orders yet
        except Exception as e:
            log.error("scheduler.research.failed", error=str(e))

    def _order_job() -> None:
        from scheduler.daily_pipeline import run_daily

        log.info("scheduler.order.fire")
        try:
            # ``--live`` is gated by the user via env vars; here we always
            # respect ``KIS_ENV``. dry_run=False allows real orders only when
            # the user explicitly set KIS_ENV=live (paper still simulates).
            run_daily(dry_run=False)
        except Exception as e:
            log.error("scheduler.order.failed", error=str(e))

    def _eod_job() -> None:
        from memory.outcomes import update_outcomes

        log.info("scheduler.eod.fire")
        try:
            update_outcomes()
        except Exception as e:
            log.error("scheduler.eod.failed", error=str(e))

    def _reflection_job() -> None:
        from datetime import date as _date

        from agents.reflection_agent import ReflectionAgent

        log.info("scheduler.reflection.fire")
        try:
            ReflectionAgent().reflect(_date.today())
        except Exception as e:
            log.error("scheduler.reflection.failed", error=str(e))

    sched.add_job(
        _research_job,
        CronTrigger(hour=research_h, minute=research_m, day_of_week="mon-fri"),
        id="research",
    )
    sched.add_job(
        _order_job,
        CronTrigger(hour=order_h, minute=order_m, day_of_week="mon-fri"),
        id="order",
    )
    sched.add_job(
        _eod_job,
        CronTrigger(hour=eod_h, minute=eod_m, day_of_week="mon-fri"),
        id="eod",
    )
    sched.add_job(
        _reflection_job,
        CronTrigger(day_of_week=refl_day, hour=refl_h, minute=refl_m),
        id="reflection",
    )
    log.info(
        "scheduler.configured",
        tz=tz_name,
        research=f"{research_h:02d}:{research_m:02d}",
        order=f"{order_h:02d}:{order_m:02d}",
        eod=f"{eod_h:02d}:{eod_m:02d}",
        reflection=f"{refl_day} {refl_h:02d}:{refl_m:02d}",
    )
    return sched


def main() -> None:
    sched = build_scheduler()

    def _shutdown(signum: int, _frame: object) -> None:
        log.info("scheduler.shutdown", signal=signum)
        sched.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown(wait=False)
    finally:
        time.sleep(0.1)


if __name__ == "__main__":
    main()
