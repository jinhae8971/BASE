"""Daily pipeline — single entry point for the whole system.

Steps:
    1. Gather data (implicit, via each agent's gather_context)
    2. Run the four research specialists
    3. Consensus aggregation
    4. Portfolio optimization
    5. Execution agent + KIS order placement
    6. Journal every decision, index into RAG memory
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

    # 1. Specialist agents
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
                context=p.model_dump(),
            )
            rag.add(
                doc_id=rec.id,
                text=f"{p.agent_name} {p.rationale}",
                metadata={"agent": p.agent_name, "as_of": as_of.isoformat(),
                          "conviction": str(p.conviction)},
            )
        except Exception as e:  # noqa: BLE001
            log.error("agent.failed", agent=agent.name, error=str(e))

    # 2. Consensus
    consensus = Consensus().aggregate(proposals, as_of)

    # 3. Optimize
    target: PortfolioTarget = PortfolioOptimizer().optimize(consensus)

    # 4. Execute (live orders only when KIS credentials present and not dry_run)
    exec_agent = ExecutionAgent()
    env = get_env()
    results: list = []

    if env.kis_app_key and env.kis_app_secret and not dry_run:
        client = KISClient()
        try:
            balance_raw = client.get_balance()
            current_positions, cash = _parse_balance(balance_raw)
            prices = _fetch_prices_for_target(client, target, current_positions)
            results = exec_agent.execute(
                target, current_positions, cash, prices, dry_run=False
            )
            # Journal execution results
            for r in results:
                journal.record(
                    agent="execution",
                    action=r.order.side.value,
                    ticker=r.order.ticker,
                    conviction=7,
                    rationale=r.message or "KIS order submitted",
                    context=r.model_dump(),
                )
        except Exception as exc:
            log.error("pipeline.execution_failed", error=str(exc))
    else:
        log.info("pipeline.dry_run_no_exec", dry_run=dry_run,
                 has_key=bool(env.kis_app_key))

    log.info("pipeline.done", positions=len(target.positions), cash=target.cash_weight)
    return {
        "target": target.model_dump(),
        "n_proposals": len(proposals),
        "execution_results": [r.model_dump() for r in results],
    }


# ---------------------------------------------------------------------------
# KIS balance parsing
# ---------------------------------------------------------------------------

def _parse_balance(balance_raw: dict[str, Any]) -> tuple[dict[str, int], float]:
    """Parse KIS get_balance() response into (positions, cash_krw).

    KIS TTTC8434R / VTTC8434R response structure:
      output1: list of holdings
        pdno      — 종목코드
        hldg_qty  — 보유수량
        prpr      — 현재가
      output2: account summary list (first element)
        dnca_tot_amt — 예수금총금액
    """
    positions: dict[str, int] = {}
    cash = 0.0

    try:
        for holding in balance_raw.get("output1", []):
            tkr = str(holding.get("pdno", "")).strip()
            qty_raw = holding.get("hldg_qty", "0")
            qty = int(str(qty_raw).replace(",", ""))
            if tkr and qty > 0:
                positions[tkr] = qty
    except Exception as exc:
        log.warning("pipeline.balance_parse_holdings_failed", error=str(exc))

    try:
        output2 = balance_raw.get("output2", [])
        if output2:
            summary = output2[0] if isinstance(output2, list) else output2
            raw = summary.get("dnca_tot_amt", "0")
            cash = float(str(raw).replace(",", ""))
    except Exception as exc:
        log.warning("pipeline.balance_parse_cash_failed", error=str(exc))

    return positions, cash


def _fetch_prices_for_target(
    client: KISClient,
    target: PortfolioTarget,
    current_positions: dict[str, int],
) -> dict[str, float]:
    """Fetch current prices for all tickers in target + current holdings."""
    tickers = set(target.positions.keys()) | set(current_positions.keys())
    prices: dict[str, float] = {}
    for tkr in tickers:
        try:
            prices[tkr] = client.get_price(tkr)
        except Exception as exc:
            log.warning("pipeline.price_fetch_failed", ticker=tkr, error=str(exc))
    return prices


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@app.command()
def cli(
    env: str = typer.Option("paper", help="paper | live"),
    dry_run: bool = typer.Option(True, "--dry-run/--live"),
) -> None:
    import os
    os.environ["KIS_ENV"] = env
    result = run_daily(dry_run=dry_run)
    typer.echo(f"Done — {result['n_proposals']} proposals, "
               f"{len(result['execution_results'])} orders")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
