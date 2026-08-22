"""APScheduler wiring — the clock behind the engine.

Four jobs, all on Asia/Seoul:

``selection``  daily at ``schedule.selection_time`` (default 09:10) — the scan
               and entry cycle.
``monitor``    every ``schedule.monitor_interval_min`` minutes — exit checks.
``eod_exit``   daily at ``schedule.eod_exit_time`` — flatten before the next
               selection so a day trade never silently becomes a swing trade.
``snapshot``   every ``schedule.snapshot_interval_min`` minutes — equity curve.

The dashboard owns this object: changing a time in the 전략 tab calls
:meth:`UpbitScheduler.reschedule`, which rebuilds the triggers in place.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from common.logging import get_logger

from .engine import KST, TradingEngine
from .store import UpbitStore, get_store
from .strategy import load_config

log = get_logger(__name__)

JOB_IDS = ("selection", "monitor", "eod_exit", "snapshot")


class UpbitScheduler:
    """Owns the background scheduler and the single shared engine instance."""

    def __init__(self, store: UpbitStore | None = None) -> None:
        self.store = store or get_store()
        self._scheduler: Any = None
        self._engine: TradingEngine | None = None
        self._lock = threading.RLock()
        self._last: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    @property
    def engine(self) -> TradingEngine:
        with self._lock:
            if self._engine is None:
                self._engine = TradingEngine(store=self.store)
            return self._engine

    def reset_engine(self) -> None:
        """Force a rebuild — used after a mode switch or a credentials change."""
        with self._lock:
            self._engine = None

    # ------------------------------------------------------------------
    def start(self) -> dict[str, Any]:
        from apscheduler.schedulers.background import BackgroundScheduler

        # APScheduler narrates every job add at INFO; our own structured events
        # already cover what matters, so keep its chatter out of the console.
        logging.getLogger("apscheduler").setLevel(logging.WARNING)

        with self._lock:
            if self._scheduler is None:
                self._scheduler = BackgroundScheduler(
                    timezone=KST,
                    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 900},
                )
            self._install_jobs()
            if not self._scheduler.running:
                self._scheduler.start()
                self.store.log_event("info", "scheduler", "스케줄러를 시작했습니다.")
                log.info("upbit.scheduler.started")
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._scheduler is not None and self._scheduler.running:
                self._scheduler.shutdown(wait=False)
                self.store.log_event("warning", "scheduler", "스케줄러를 중지했습니다.")
                log.info("upbit.scheduler.stopped")
            self._scheduler = None
        return self.status()

    def reschedule(self) -> dict[str, Any]:
        """Re-read the config and rebuild every trigger."""
        with self._lock:
            self.reset_engine()
            if self._scheduler is None:
                return self.status()
            self._install_jobs()
        return self.status()

    # ------------------------------------------------------------------
    def _install_jobs(self) -> None:
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        config = load_config(self.store)
        sched = config.schedule
        for job_id in JOB_IDS:
            if self._scheduler.get_job(job_id):
                self._scheduler.remove_job(job_id)

        if not sched.enabled:
            log.info("upbit.scheduler.disabled_by_config")
            return

        sel_h, sel_m = (int(x) for x in sched.selection_time.split(":"))
        eod_h, eod_m = (int(x) for x in sched.eod_exit_time.split(":"))

        self._scheduler.add_job(
            self._run_selection,
            CronTrigger(hour=sel_h, minute=sel_m, timezone=KST),
            id="selection",
            name=f"일일 종목 선정 ({sched.selection_time} KST)",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._run_monitor,
            IntervalTrigger(minutes=sched.monitor_interval_min, timezone=KST),
            id="monitor",
            name=f"포지션 모니터링 ({sched.monitor_interval_min}분 간격)",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._run_eod_exit,
            CronTrigger(hour=eod_h, minute=eod_m, timezone=KST),
            id="eod_exit",
            name=f"일일 강제 청산 ({sched.eod_exit_time} KST)",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._run_snapshot,
            IntervalTrigger(minutes=sched.snapshot_interval_min, timezone=KST),
            id="snapshot",
            name=f"자산 스냅샷 ({sched.snapshot_interval_min}분 간격)",
            replace_existing=True,
        )
        log.info(
            "upbit.scheduler.jobs_installed",
            selection=sched.selection_time,
            monitor_min=sched.monitor_interval_min,
            eod=sched.eod_exit_time,
        )

    # ------------------------------------------------------------------
    # Job bodies — every one is wrapped so a failure never kills the scheduler.
    # ------------------------------------------------------------------
    def _record(self, name: str, result: dict[str, Any]) -> dict[str, Any]:
        self._last[name] = {"at": datetime.now(KST).isoformat(timespec="seconds"), **result}
        return result

    def _guarded(self, name: str, fn: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return self._record(name, fn(**kwargs))
        except Exception as exc:  # keep the scheduler alive
            log.error("upbit.scheduler.job_failed", job=name, error=str(exc))
            self.store.log_event("error", "scheduler", f"{name} 작업 실패: {exc}")
            return self._record(name, {"error": str(exc)})

    def _run_selection(self) -> dict[str, Any]:
        return self._guarded("selection", self.engine.run_selection)

    def _run_monitor(self) -> dict[str, Any]:
        return self._guarded("monitor", self.engine.monitor_positions)

    def _run_eod_exit(self) -> dict[str, Any]:
        return self._guarded("eod_exit", self.engine.monitor_positions, force_exit=True)

    def _run_snapshot(self) -> dict[str, Any]:
        return self._guarded("snapshot", self.engine.snapshot_equity)

    # ------------------------------------------------------------------
    def trigger_now(self, job: str) -> dict[str, Any]:
        """Run one job immediately from the dashboard's 실행 buttons."""
        runners = {
            "selection": self._run_selection,
            "monitor": self._run_monitor,
            "eod_exit": self._run_eod_exit,
            "snapshot": self._run_snapshot,
        }
        if job not in runners:
            raise ValueError(f"알 수 없는 작업: {job}")
        return runners[job]()

    def status(self) -> dict[str, Any]:
        config = load_config(self.store)
        running = bool(self._scheduler and self._scheduler.running)
        jobs = []
        if running:
            for job in self._scheduler.get_jobs():
                jobs.append(
                    {
                        "id": job.id,
                        "name": job.name,
                        "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                    }
                )
        return {
            "running": running,
            "enabled": config.schedule.enabled,
            "mode": config.mode,
            "timezone": "Asia/Seoul",
            "now": datetime.now(KST).isoformat(timespec="seconds"),
            "jobs": jobs,
            "last_results": self._last,
        }


_SCHEDULER: UpbitScheduler | None = None


def get_scheduler() -> UpbitScheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = UpbitScheduler()
    return _SCHEDULER
