"""Minimal async daily scheduler.

Why not APScheduler: the system has exactly one scheduled job and needs to
cooperate with `asyncio`, HALT-file checks, and graceful Ctrl-C shutdown.
A ~30-line asyncio loop is simpler, has no extra dependency, and is
trivially testable by injecting a fake clock.

Usage:
    sched = DailyScheduler(workflow, interval_seconds=24 * 3600)
    await sched.run()  # blocks; tick at most once per interval

Design notes:
  - Each tick reads `killswitch.is_halted()`; if the HALT file exists we
    still sleep through the interval but emit an alert instead of running
    the workflow. This gives the operator a single place to freeze trading
    without tearing down the process.
  - Uncaught exceptions inside the workflow are logged and alerted but do
    NOT terminate the loop -- the system is supposed to run unattended.
  - Test-mode: pass `max_ticks` and `now_fn` / `sleep_fn` to step through
    a deterministic fake clock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from src.execution.killswitch import is_halted
from src.logging import get_logger
from src.orchestrator.alerts import TelegramAlerter
from src.orchestrator.daily_workflow import DailyWorkflow

log = get_logger("scheduler")

DAY_SECONDS = 24 * 3600


class DailyScheduler:
    def __init__(
        self,
        workflow: DailyWorkflow,
        interval_seconds: int = DAY_SECONDS,
        alerter: TelegramAlerter | None = None,
        *,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        halt_check: Callable[[], bool] = is_halted,
        max_ticks: int | None = None,
    ) -> None:
        self.workflow = workflow
        self.interval = interval_seconds
        self.alerter = alerter
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._sleep = sleep_fn or asyncio.sleep
        self._halt_check = halt_check
        self._max_ticks = max_ticks
        self._stopping = asyncio.Event()
        self.runs_completed = 0
        self.runs_halted = 0
        self.runs_failed = 0

    def request_stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        tick = 0
        while not self._stopping.is_set():
            tick += 1
            now = self._now()
            log.info("scheduler.tick", tick=tick, at=now.isoformat())

            if self._halt_check():
                self.runs_halted += 1
                log.warning("scheduler.halt_file_present")
                if self.alerter is not None:
                    await self.alerter.send(
                        "Trading halted -- HALT file present", level="warn"
                    )
            else:
                try:
                    result = await self.workflow.run()
                    if result.get("halted"):
                        self.runs_halted += 1
                    else:
                        self.runs_completed += 1
                except Exception as exc:  # noqa: BLE001
                    self.runs_failed += 1
                    log.error("scheduler.run_failed", err=str(exc))
                    if self.alerter is not None:
                        await self.alerter.send(
                            f"scheduler run failed: {exc}", level="error"
                        )

            if self._max_ticks is not None and tick >= self._max_ticks:
                log.info("scheduler.max_ticks_reached", ticks=tick)
                return

            await self._sleep(float(self.interval))
