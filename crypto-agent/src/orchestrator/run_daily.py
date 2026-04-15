"""CLI entrypoint: `python -m src.orchestrator.run_daily [--dry-run]`."""

from __future__ import annotations

import asyncio
import json

import typer

from src.orchestrator.daily_workflow import DailyWorkflow

app = typer.Typer(add_completion=False, no_args_is_help=False)


@app.command()
def run(dry_run: bool = typer.Option(False, "--dry-run", help="Force dry mode")) -> None:
    """Run one full daily cycle."""
    if dry_run:
        import os

        os.environ["TRADING_MODE"] = "dry"

    wf = DailyWorkflow()
    result = asyncio.run(wf.run())
    typer.echo(json.dumps(result, default=str, indent=2))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
