"""Daily pipeline — single entry point for the whole system.

Steps:
    1. Gather data (implicit, via each agent's gather_context)
    2. Run the four research specialists in parallel-ish
    3. Consensus aggregation
    4. Portfolio optimization
    5. Execution agent (LLM sanity-check optional) + KIS order placement
    6. Journal every decision, append RAG memory
"""
from __future__ import annotations

from datetime import date
from typing import Any

import typer

from agents import (
    ExecutionAgent,
    MacroAgent,
    QuantAgent,
    SectorAgent,
    ValueAgent,
)
from broker.kis_client import KISClient
from common.config import get_env
from common.logging import get_logger, setup_logging
from common.types import PortfolioTarget
from memory.journal import DecisionJournal
from orchestrator import Consensus, PortfolioOptimizer

log = get_logger(__name__)
app = typer.Typer(add_completion=False)


def run_daily(as_of: date | None = None, *, dry_run: bool = True) -> dict[str, Any]:
    setup_logging()
    as_of = as_of or date.today()
    log.info("pipeline.start", as_of=as_of.isoformat(), dry_run=dry_run)

    # 1. Specialist agents
    specialists = [MacroAgent(), SectorAgent(), ValueAgent(), QuantAgent()]
    proposals = []
    journal = DecisionJournal()
    for agent in specialists:
        try:
            p = agent.run(as_of)
            proposals.append(p)
            journal.record(
                agent=p.agent_name,
                action="PROPOSE",
                ticker=None,
                conviction=p.conviction,
                rationale=p.rationale,
                context=p.model_dump(),
            )
        except Exception as e:  # noqa: BLE001
            log.error("agent.failed", agent=agent.name, error=str(e))

    # 2. Consensus
    consensus = Consensus().aggregate(proposals, as_of)

    # 3. Optimize
    target: PortfolioTarget = PortfolioOptimizer().optimize(consensus)

    # 4. Execute
    exec_agent = ExecutionAgent()
    env = get_env()
    results: list = []
    if env.kis_app_key and env.kis_app_secret and not dry_run:
        client = KISClient()
        balance = client.get_balance()
        # TODO(Phase 1): parse balance into current_positions + cash + prices
        current_positions: dict[str, int] = {}
        cash: float = 0.0
        prices: dict[str, float] = {}
        results = exec_agent.execute(
            target, current_positions, cash, prices, dry_run=False
        )
    else:
        log.info("pipeline.dry_run_no_exec")

    log.info("pipeline.done", positions=len(target.positions), cash=target.cash_weight)
    return {
        "target": target.model_dump(),
        "n_proposals": len(proposals),
        "execution_results": [r.model_dump() for r in results],
    }


@app.command()
def cli(
    env: str = typer.Option("paper", help="paper | live"),
    dry_run: bool = typer.Option(True, "--dry-run/--live"),
) -> None:
    import os

    os.environ["KIS_ENV"] = env
    run_daily(dry_run=dry_run)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
