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
    morning_h, morning_m = _hhmm(
        str(get_setting("scheduler.morning_report_time", "08:30")), "08:30"
    )
    order_h, order_m = _hhmm(str(get_setting("scheduler.order_time", "09:05")), "09:05")
    eod_h, eod_m = _hhmm(str(get_setting("scheduler.eod_review_time", "16:00")), "16:00")
    refl_day = str(get_setting("scheduler.reflection_day", "fri"))[:3].lower()
    refl_h, refl_m = _hhmm(str(get_setting("scheduler.reflection_time", "18:00")), "18:00")
    hb_h, hb_m = _hhmm(str(get_setting("scheduler.heartbeat_time", "08:00")), "08:00")
    jitter = int(get_setting("scheduler.jitter_seconds", 60))
    intraday_enabled = bool(get_setting("scheduler.intraday_stops_enabled", True))
    intraday_start_h, intraday_start_m = _hhmm(
        str(get_setting("scheduler.intraday_stops_start", "10:00")), "10:00"
    )
    intraday_end_h, _intraday_end_m = _hhmm(
        str(get_setting("scheduler.intraday_stops_end", "15:00")), "15:00"
    )
    intraday_interval = max(
        5, int(get_setting("scheduler.intraday_stops_interval_min", 30))
    )

    def _research_job() -> None:
        from scheduler.daily_pipeline import research_phase

        log.info("scheduler.research.fire")
        try:
            research_phase()
        except Exception as e:
            log.error("scheduler.research.failed", error=str(e))

    def _order_job() -> None:
        from scheduler.daily_pipeline import order_phase

        log.info("scheduler.order.fire")
        try:
            # ``KIS_ENV=paper`` keeps us in the simulator; live env actually trades.
            order_phase(dry_run=False)
        except Exception as e:
            log.error("scheduler.order.failed", error=str(e))

    def _eod_job() -> None:
        from scheduler.daily_pipeline import eod_phase

        log.info("scheduler.eod.fire")
        try:
            eod_phase()
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

    def _morning_job() -> None:
        from scheduler.morning_report import build_morning_report

        log.info("scheduler.morning_report.fire")
        try:
            build_morning_report()
        except Exception as e:
            log.error("scheduler.morning_report.failed", error=str(e))

    def _intraday_stops_job() -> None:
        from scheduler.daily_pipeline import intraday_stops_phase

        try:
            intraday_stops_phase()
        except Exception as e:
            log.error("scheduler.intraday_stops.failed", error=str(e))

    def _heartbeat_job() -> None:
        from scheduler.morning_report import heartbeat

        try:
            heartbeat()
        except Exception as e:
            log.error("scheduler.heartbeat.failed", error=str(e))

    sched.add_job(
        _research_job,
        CronTrigger(hour=research_h, minute=research_m, day_of_week="mon-fri"),
        id="research",
    )
    sched.add_job(
        _morning_job,
        CronTrigger(hour=morning_h, minute=morning_m, day_of_week="mon-fri"),
        id="morning_report",
    )
    sched.add_job(
        _order_job,
        # ±jitter on the order time → harder for HFTs to pattern-match.
        CronTrigger(
            hour=order_h, minute=order_m, day_of_week="mon-fri", jitter=jitter
        ),
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
    sched.add_job(
        _heartbeat_job,
        CronTrigger(hour=hb_h, minute=hb_m),
        id="heartbeat",
    )
    if intraday_enabled:
        # Build a comma-separated minute list (e.g. "0,30") for the cron, and
        # an hour range that excludes the lunch-break-hour-only edge case.
        if intraday_interval >= 60:
            minute_list = "0"
        else:
            minutes = list(range(intraday_start_m, 60, intraday_interval))
            minute_list = ",".join(str(m) for m in minutes)
        # Hour range is end_h - 1 inclusive (the last cron fires at end_h:00,
        # which is intraday_end's first minute → keep it in the range).
        hour_expr = f"{intraday_start_h}-{intraday_end_h}"
        sched.add_job(
            _intraday_stops_job,
            CronTrigger(
                hour=hour_expr,
                minute=minute_list,
                day_of_week="mon-fri",
            ),
            id="intraday_stops",
        )
    log.info(
        "scheduler.configured",
        tz=tz_name,
        research=f"{research_h:02d}:{research_m:02d}",
        morning=f"{morning_h:02d}:{morning_m:02d}",
        order=f"{order_h:02d}:{order_m:02d}",
        eod=f"{eod_h:02d}:{eod_m:02d}",
        reflection=f"{refl_day} {refl_h:02d}:{refl_m:02d}",
        heartbeat=f"{hb_h:02d}:{hb_m:02d}",
        jitter_s=jitter,
        intraday_stops=(
            f"{intraday_start_h:02d}:{intraday_start_m:02d}-{intraday_end_h:02d}:00 "
            f"every {intraday_interval}min"
            if intraday_enabled
            else "off"
        ),
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
