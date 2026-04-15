"""Emergency kill switch — liquidate every position at market.

Usage:
    python scripts/kill_switch.py --confirm I-UNDERSTAND

This script will:
    1. Query KIS balance for all current positions
    2. Submit market SELL orders for every holding
    3. Log every action to the decision journal

Requires an explicit `--confirm I-UNDERSTAND` flag so it cannot run accidentally.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import typer  # noqa: E402

from broker.kis_client import KISClient  # noqa: E402
from common.logging import get_logger, setup_logging  # noqa: E402
from common.types import Order, Side  # noqa: E402
from memory.journal import DecisionJournal  # noqa: E402

log = get_logger(__name__)
app = typer.Typer(add_completion=False)


@app.command()
def kill(
    confirm: str = typer.Option("", help="Must be 'I-UNDERSTAND' to proceed"),
) -> None:
    if confirm != "I-UNDERSTAND":
        typer.secho(
            "Refusing to run without --confirm I-UNDERSTAND",
            fg="red",
            err=True,
        )
        raise typer.Exit(code=2)

    setup_logging()
    client = KISClient()
    balance = client.get_balance()
    journal = DecisionJournal()

    positions = balance.get("output1", [])
    if not positions:
        typer.echo("No positions to liquidate.")
        return

    for pos in positions:
        ticker = pos.get("pdno")
        qty = int(pos.get("hldg_qty", 0))
        if not ticker or qty <= 0:
            continue
        order = Order(ticker=ticker, side=Side.SELL, quantity=qty, order_type="market")
        result = client.place_order(order)
        journal.record(
            agent="kill_switch",
            action="LIQUIDATE",
            ticker=ticker,
            conviction=10,
            rationale="Manual kill switch activation",
            context={"result": result.model_dump(), "ts": datetime.utcnow().isoformat()},
        )
        log.info("kill_switch.sent", ticker=ticker, qty=qty, status=result.status)


if __name__ == "__main__":
    app()
