"""Emergency kill switch — liquidate every position at market.

Usage:
    python scripts/kill_switch.py --confirm I-UNDERSTAND

This script will:
    1. Query KIS balance via ``KISClient.get_account_state`` (parsed view)
    2. Submit market SELL orders for every holding
    3. Notify Slack/Telegram of every action
    4. Log every action to the decision journal

Requires an explicit ``--confirm I-UNDERSTAND`` flag so it cannot run accidentally.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import typer  # noqa: E402

from broker.kis_client import KISClient  # noqa: E402
from common.logging import get_logger, setup_logging  # noqa: E402
from common.notifications import notify_error, notify_warning  # noqa: E402
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
    try:
        state = client.get_account_state()
    except Exception as e:
        notify_error("Kill switch FAILED to fetch balance", str(e))
        log.error("kill_switch.balance_failed", error=str(e))
        raise typer.Exit(code=1) from e

    journal = DecisionJournal()
    positions = state.get("positions") or {}
    if not positions:
        typer.echo("No positions to liquidate.")
        notify_warning("Kill switch invoked", "No positions to liquidate.")
        return

    notify_warning(
        "KILL SWITCH ACTIVATED",
        f"Liquidating {len(positions)} positions at market.",
        cash=state.get("cash"),
        nav=state.get("nav"),
    )

    for ticker, qty in positions.items():
        if qty <= 0:
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
        if result.status == "rejected":
            notify_error(
                "Kill switch order rejected",
                result.message,
                ticker=ticker,
                qty=qty,
            )


if __name__ == "__main__":
    app()
