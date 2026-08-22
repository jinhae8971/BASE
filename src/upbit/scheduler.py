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

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from common.logging import get_logger

from .engine import KST, TradingEngine
from .store import UpbitStore, default_db_path, get_store
from .strategy import load_config

log = get_logger(__name__)

JOB_IDS = ("selection", "monitor", "eod_exit", "snapshot", "maintenance")

# How often the watchdog thread checks that the scheduler is still alive.
WATCHDOG_INTERVAL_SEC = 60
# A monitor cycle is considered stale after this multiple of its own interval.
STALE_INTERVAL_MULTIPLIER = 3
# Grace after startup before staleness is judged, so a fresh boot is never "stale".
STARTUP_GRACE_SEC = 180


@dataclass
class CycleState:
    """Liveness record for one job — what health checks read."""

    last_started_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str = ""
    consecutive_failures: int = 0
    runs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_started_at": _iso(self.last_started_at),
            "last_success_at": _iso(self.last_success_at),
            "last_error_at": _iso(self.last_error_at),
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "runs": self.runs,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


class UpbitScheduler:
    """Owns the background scheduler and the single shared engine instance."""

    def __init__(self, store: UpbitStore | None = None) -> None:
        self.store = store or get_store()
        self._scheduler: Any = None
        self._engine: TradingEngine | None = None
        self._lock = threading.RLock()
        self._last: dict[str, dict[str, Any]] = {}
        self._cycles: dict[str, CycleState] = {}
        self._started_at: datetime | None = None
        self._watchdog: threading.Thread | None = None
        self._watchdog_stop = threading.Event()
        self._watchdog_revivals = 0

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

        with self._lock:
            if self._scheduler is None:
                self._scheduler = BackgroundScheduler(
                    timezone=KST,
                    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 900},
                )
            self._install_jobs()
            if not self._scheduler.running:
                self._scheduler.start()
                self._started_at = datetime.now(KST)
                self.store.log_event("info", "scheduler", "스케줄러를 시작했습니다.")
                log.info("upbit.scheduler.started")
            self._start_watchdog()

        # Bring the journal back in line with the exchange before the first cycle:
        # a crash between an order and its DB row would otherwise leave a phantom.
        try:
            self.engine.reconcile()
        except Exception as exc:  # never block startup on this
            log.error("upbit.reconcile.failed", error=str(exc))
            self.store.log_event("error", "reconcile", f"기동 정합성 점검 실패: {exc}")
        return self.status()

    def stop(self, *, wait: bool = True) -> dict[str, Any]:
        """Stop the scheduler. ``wait`` lets an in-flight cycle finish first —
        killing a scan mid-order is how a trade goes unrecorded."""
        self._stop_watchdog()
        with self._lock:
            if self._scheduler is not None and self._scheduler.running:
                self._scheduler.shutdown(wait=wait)
                self.store.log_event("warning", "scheduler", "스케줄러를 중지했습니다.")
                log.info("upbit.scheduler.stopped", waited=wait)
            self._scheduler = None
            self._started_at = None
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

        if config.maintenance.enabled:
            mnt_h, mnt_m = (int(x) for x in config.maintenance.time.split(":"))
            self._scheduler.add_job(
                self._run_maintenance,
                CronTrigger(hour=mnt_h, minute=mnt_m, timezone=KST),
                id="maintenance",
                name=f"백업·정리 ({config.maintenance.time} KST)",
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

    def cycle_state(self, name: str) -> CycleState:
        return self._cycles.setdefault(name, CycleState())

    def _guarded(self, name: str, fn: Any, **kwargs: Any) -> dict[str, Any]:
        state = self.cycle_state(name)
        state.last_started_at = datetime.now(KST)
        state.runs += 1
        try:
            result = fn(**kwargs)
        except Exception as exc:  # keep the scheduler alive
            state.last_error_at = datetime.now(KST)
            state.last_error = f"{type(exc).__name__}: {exc}"
            state.consecutive_failures += 1
            log.error(
                "upbit.scheduler.job_failed",
                job=name,
                error=str(exc),
                consecutive=state.consecutive_failures,
            )
            self.store.log_event(
                "error",
                "scheduler",
                f"{name} 작업 실패 ({state.consecutive_failures}회 연속): {exc}",
            )
            return self._record(name, {"error": str(exc)})

        # An engine that swallowed its own error still reports one — count it.
        if isinstance(result, dict) and result.get("error"):
            state.last_error_at = datetime.now(KST)
            state.last_error = str(result["error"])
            state.consecutive_failures += 1
        else:
            state.last_success_at = datetime.now(KST)
            state.consecutive_failures = 0
        return self._record(name, result)

    def _run_selection(self) -> dict[str, Any]:
        return self._guarded("selection", self.engine.run_selection)

    def _run_monitor(self) -> dict[str, Any]:
        return self._guarded("monitor", self.engine.monitor_positions)

    def _run_eod_exit(self) -> dict[str, Any]:
        return self._guarded("eod_exit", self.engine.monitor_positions, force_exit=True)

    def _run_snapshot(self) -> dict[str, Any]:
        return self._guarded("snapshot", self.engine.snapshot_equity)

    def _run_maintenance(self) -> dict[str, Any]:
        return self._guarded("maintenance", self._maintenance)

    def _maintenance(self) -> dict[str, Any]:
        """Daily housekeeping: back the DB up, prune aged rows, reclaim space."""
        config = load_config(self.store)
        mnt = config.maintenance
        before = self.store.database_size_bytes()

        backup_path: str | None = None
        if mnt.backup_keep > 0:
            backup_dir = default_db_path().parent / "backups"
            backup_path = str(self.store.backup(backup_dir, keep=mnt.backup_keep))

        removed = self.store.prune(
            analyses_days=mnt.analyses_retention_days,
            events_days=mnt.events_retention_days,
            snapshots_days=mnt.snapshots_retention_days,
        )
        if mnt.vacuum and any(removed.values()):
            self.store.vacuum()

        after = self.store.database_size_bytes()
        summary = {
            "backup": backup_path,
            "removed": removed,
            "db_bytes_before": before,
            "db_bytes_after": after,
        }
        self.store.log_event(
            "info",
            "maintenance",
            f"유지보수 완료 — 백업 {'생성' if backup_path else '생략'}, "
            f"정리 {sum(removed.values())}행, DB {before / 1e6:.1f}→{after / 1e6:.1f} MB",
            summary,
        )
        log.info("upbit.maintenance.done", removed=sum(removed.values()), db_bytes=after)
        return summary

    # ------------------------------------------------------------------
    def trigger_now(self, job: str) -> dict[str, Any]:
        """Run one job immediately from the dashboard's 실행 buttons."""
        runners = {
            "selection": self._run_selection,
            "monitor": self._run_monitor,
            "eod_exit": self._run_eod_exit,
            "snapshot": self._run_snapshot,
            "maintenance": self._run_maintenance,
        }
        if job not in runners:
            raise ValueError(f"알 수 없는 작업: {job}")
        return runners[job]()

    # ------------------------------------------------------------------
    # Watchdog — the scheduler thread dying must not look like a healthy system
    # ------------------------------------------------------------------
    def _start_watchdog(self) -> None:
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        self._watchdog_stop.clear()
        self._watchdog = threading.Thread(
            target=self._watchdog_loop, name="upbit-watchdog", daemon=True
        )
        self._watchdog.start()
        log.info("upbit.watchdog.started", interval_sec=WATCHDOG_INTERVAL_SEC)

    def _stop_watchdog(self) -> None:
        self._watchdog_stop.set()
        self._watchdog = None

    def _watchdog_loop(self) -> None:
        """Revive the scheduler if its thread dies while it is meant to be running.

        APScheduler survives job exceptions on its own, but a hard failure in the
        executor (or an OOM-killed thread) leaves HTTP serving happily while
        nothing trades. Silence there is the worst outcome for an unattended
        system, so check and restart.
        """
        while not self._watchdog_stop.wait(WATCHDOG_INTERVAL_SEC):
            try:
                if not load_config(self.store).schedule.enabled:
                    continue
                with self._lock:
                    alive = bool(self._scheduler and self._scheduler.running)
                if alive:
                    continue

                self._watchdog_revivals += 1
                log.error("upbit.watchdog.reviving", revivals=self._watchdog_revivals)
                self.store.log_event(
                    "error",
                    "scheduler",
                    f"스케줄러가 멈춰 있어 재기동합니다 ({self._watchdog_revivals}회차).",
                )
                self.start()
            except Exception as exc:  # the watchdog itself must never die
                log.error("upbit.watchdog.error", error=str(exc))

    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Is this thing actually trading? Answers more than 'HTTP is up'."""
        config = load_config(self.store)
        running = bool(self._scheduler and self._scheduler.running)
        now = datetime.now(KST)

        problems: list[str] = []
        if config.schedule.enabled and not running:
            problems.append("스케줄러가 실행 중이 아닙니다.")

        monitor = self.cycle_state("monitor")
        booted_recently = (
            self._started_at is None
            or (now - self._started_at).total_seconds() < STARTUP_GRACE_SEC
        )
        stale_after = timedelta(
            minutes=config.schedule.monitor_interval_min * STALE_INTERVAL_MULTIPLIER
        )
        if config.schedule.enabled and running and not booted_recently:
            if monitor.last_success_at is None:
                problems.append("모니터링 사이클이 아직 한 번도 성공하지 못했습니다.")
            elif now - monitor.last_success_at > stale_after:
                age = int((now - monitor.last_success_at).total_seconds() / 60)
                problems.append(f"모니터링이 {age}분째 성공하지 못했습니다.")

        for name, state in self._cycles.items():
            if state.consecutive_failures >= 3:
                problems.append(f"{name} 작업이 {state.consecutive_failures}회 연속 실패했습니다.")

        return {
            "healthy": not problems,
            "problems": problems,
            "running": running,
            "started_at": _iso(self._started_at),
            "uptime_sec": int((now - self._started_at).total_seconds()) if self._started_at else 0,
            "watchdog_alive": bool(self._watchdog and self._watchdog.is_alive()),
            "watchdog_revivals": self._watchdog_revivals,
            "cycles": {name: state.to_dict() for name, state in self._cycles.items()},
        }

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
            "health": self.health(),
        }


_SCHEDULER: UpbitScheduler | None = None


def get_scheduler() -> UpbitScheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = UpbitScheduler()
    return _SCHEDULER
