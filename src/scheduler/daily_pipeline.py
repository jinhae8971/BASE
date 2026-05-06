"""Daily pipeline — single entry point for the whole system.

Steps:
    1. Each specialist agent gathers its own context and produces an
       ``AgentProposal``.
    2. Consensus aggregates those proposals.
    3. PortfolioOptimizer turns the consensus into a ``PortfolioTarget``.
    4. ExecutionAgent applies risk guards and submits orders to KIS
       (or dry-runs).
    5. Every step is journaled, and accepted picks are pushed to RAG memory.
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
from data.market import fetch_latest_prices
from data.universe import sector_map
from memory.journal import DecisionJournal
from memory.rag import RAGMemory
from orchestrator import Consensus, PortfolioOptimizer

log = get_logger(__name__)
app = typer.Typer(add_completion=False)


def run_daily(as_of: date | None = None, *, dry_run: bool = True) -> dict[str, Any]:
    setup_logging()
    as_of = as_of or date.today()
    log.info("pipeline.start", as_of=as_of.isoformat(), dry_run=dry_run)

    journal = DecisionJournal()
    rag = RAGMemory()

    # 1. Specialist agents -------------------------------------------------
    specialists = [MacroAgent(), SectorAgent(), ValueAgent(), QuantAgent()]
    proposals = []
    for agent in specialists:
        try:
            p = agent.run(as_of)
            proposals.append(p)
            rec = journal.record(
                agent=p.agent_name,
                action="PROPOSE",
                ticker=None,
                conviction=p.conviction,
                rationale=p.rationale,
                context=p.model_dump(mode="json"),
            )
            rag.add(
                doc_id=rec.id,
                text=p.rationale,
                metadata={"agent": p.agent_name, "as_of": as_of.isoformat()},
            )
            for pick in p.picks:
                journal.record(
                    agent=p.agent_name,
                    action="PICK",
                    ticker=pick.ticker,
                    conviction=p.conviction,
                    rationale=pick.rationale or "",
                    context={
                        "score": pick.score,
                        "side": pick.side.value,
                        "target_weight": pick.target_weight,
                    },
                )
        except Exception as e:
            log.error("agent.failed", agent=agent.name, error=str(e))

    # 2. Consensus + 3. Optimize ------------------------------------------
    consensus = Consensus().aggregate(proposals, as_of)
    smap = sector_map(as_of)
    target: PortfolioTarget = PortfolioOptimizer().optimize(consensus, sector_map=smap)
    journal.record(
        agent="orchestrator",
        action="TARGET",
        ticker=None,
        conviction=10,
        rationale=target.rationale,
        context=target.model_dump(mode="json"),
    )

    # 4. Execute ----------------------------------------------------------
    exec_agent = ExecutionAgent()
    env = get_env()
    results: list = []

    if env.kis_app_key and env.kis_app_secret:
        try:
            client = KISClient()
            state = client.get_account_state()
            current_positions = state["positions"]
            cash = state["cash"]
            position_tickers = list(set(current_positions) | set(target.positions))
            prices = client.get_prices(position_tickers) if position_tickers else {}
            # Fallback: backfill missing prices from pykrx/FDR
            if any(t not in prices for t in position_tickers):
                fallback = fetch_latest_prices(
                    [t for t in position_tickers if t not in prices], as_of=as_of
                )
                prices.update(fallback)
            results = exec_agent.execute(
                target, current_positions, cash, prices, dry_run=dry_run
            )
            for r in results:
                journal.record(
                    agent="execution",
                    action=f"ORDER_{r.order.side.value}",
                    ticker=r.order.ticker,
                    conviction=10,
                    rationale=r.message,
                    context=r.model_dump(mode="json"),
                )
        except Exception as e:
            log.error("pipeline.execution_failed", error=str(e))
    else:
        log.info("pipeline.no_kis_credentials")

    log.info(
        "pipeline.done",
        positions=len(target.positions),
        cash=round(target.cash_weight, 4),
        n_orders=len(results),
        dry_run=dry_run,
    )
    return {
        "as_of": as_of.isoformat(),
        "target": target.model_dump(mode="json"),
        "n_proposals": len(proposals),
        "execution_results": [r.model_dump(mode="json") for r in results],
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
