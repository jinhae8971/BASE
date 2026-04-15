"""CLI entrypoint.

Two modes:
  - `python -m src.orchestrator.run_daily once [--dry-run]`
      Execute exactly one DailyWorkflow run and print the result dict.
  - `python -m src.orchestrator.run_daily schedule [--interval-hours 24]`
      Run the DailyScheduler loop until interrupted. Each tick invokes one
      DailyWorkflow; failures are logged + alerted but do not kill the loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from typing import Any

import typer

from src.orchestrator.alerts import TelegramAlerter
from src.orchestrator.daily_workflow import DailyWorkflow
from src.orchestrator.scheduler import DAY_SECONDS, DailyScheduler

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _force_dry_mode() -> None:
    os.environ["TRADING_MODE"] = "dry"


@app.command()
def once(
    dry_run: bool = typer.Option(False, "--dry-run", help="Force dry mode"),
    universe_size: int = typer.Option(20, "--universe-size"),
) -> None:
    """Run one full daily cycle and print the result JSON."""
    if dry_run:
        _force_dry_mode()
    wf = DailyWorkflow()
    result = asyncio.run(wf.run(universe_size=universe_size))
    typer.echo(json.dumps(result, default=str, indent=2))


@app.command()
def schedule(
    interval_hours: float = typer.Option(24.0, "--interval-hours"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Run the daily scheduler loop until Ctrl-C or SIGTERM."""
    if dry_run:
        _force_dry_mode()

    alerter = TelegramAlerter()
    wf = DailyWorkflow(alerter=alerter)
    sched = DailyScheduler(
        workflow=wf,
        interval_seconds=int(interval_hours * 3600),
        alerter=alerter,
    )

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, sched.request_stop)
            except NotImplementedError:  # Windows
                pass
        try:
            await sched.run()
        finally:
            await alerter.aclose()

    asyncio.run(_run())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
