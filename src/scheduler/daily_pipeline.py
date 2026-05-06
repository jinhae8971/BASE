"""Daily pipeline — research and order phases.

Two entry points:
    research_phase(as_of)  — run the 4 specialists, build a consensus + target,
                             persist it. Always dry-run (no broker calls).
    order_phase(as_of)     — load the saved target, gather live KIS state +
                             quotes + ADV, hand off to ExecutionAgent.

``run_daily`` is a convenience that does research + order in sequence (used
by the standalone CLI and the legacy single-step CI flow).
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
from common.notifications import notify_error, notify_info, notify_warning
from common.types import PortfolioTarget
from data.market import fetch_latest_prices
from data.universe import get_universe, sector_map
from memory.journal import DecisionJournal
from memory.rag import RAGMemory
from orchestrator import Consensus, PortfolioOptimizer
from portfolio.risk_guards import (
    daily_executed_notional,
    load_recent_equity,
    persist_nav,
)

from . import state as state_store

log = get_logger(__name__)
app = typer.Typer(add_completion=False)


# ----------------------------------------------------------------------
# Phase 1 — research
# ----------------------------------------------------------------------
def research_phase(as_of: date | None = None) -> dict[str, Any]:
    setup_logging()
    as_of = as_of or date.today()
    log.info("research.start", as_of=as_of.isoformat())

    journal = DecisionJournal()
    rag = RAGMemory()

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
            notify_error("Agent failed", str(e), agent=agent.name)

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
    state_store.save_target(
        target,
        extra={
            "n_proposals": len(proposals),
            "consensus_regime": str(consensus.get("regime")),
        },
    )
    notify_info(
        "Research done",
        target.rationale or "(no rationale)",
        positions=len(target.positions),
        cash=f"{target.cash_weight:.0%}",
    )
    log.info(
        "research.done",
        positions=len(target.positions),
        cash=round(target.cash_weight, 4),
    )
    return {
        "as_of": as_of.isoformat(),
        "target": target.model_dump(mode="json"),
        "n_proposals": len(proposals),
    }


# ----------------------------------------------------------------------
# Phase 2 — execution
# ----------------------------------------------------------------------
def order_phase(as_of: date | None = None, *, dry_run: bool = True) -> dict[str, Any]:
    setup_logging()
    as_of = as_of or date.today()
    log.info("order.start", as_of=as_of.isoformat(), dry_run=dry_run)

    loaded = state_store.load_target(as_of)
    if loaded is None:
        log.warning("order.no_saved_target_running_research")
        research_phase(as_of)
        loaded = state_store.load_target(as_of)
        if loaded is None:
            return {"as_of": as_of.isoformat(), "error": "no target available"}
    target, _extra = loaded

    journal = DecisionJournal()
    env = get_env()
    if not (env.kis_app_key and env.kis_app_secret):
        log.info("order.no_kis_credentials")
        return {"as_of": as_of.isoformat(), "skipped": "no KIS credentials"}

    client = KISClient()
    state = client.get_account_state()
    current_positions = state["positions"]
    cash = state["cash"]
    nav = state["nav"] or (cash + sum(
        p * state["prices"].get(t, 0.0) for t, p in current_positions.items()
    ))

    position_tickers = list(set(current_positions) | set(target.positions))
    prices = client.get_prices(position_tickers) if position_tickers else {}
    if any(t not in prices for t in position_tickers):
        fallback = fetch_latest_prices(
            [t for t in position_tickers if t not in prices], as_of=as_of
        )
        prices.update(fallback)
    orderbooks = client.get_orderbooks(position_tickers) if position_tickers else {}

    # 20-day ADV from the universe (already filtered)
    adv_map = {r["ticker"]: float(r.get("adv", 0)) for r in get_universe(as_of)}

    # Yesterday's NAV for daily-loss kill, equity history for MDD trigger
    eq = load_recent_equity()
    prev_nav = float(eq.iloc[-1]) if not eq.empty else None
    today_executed = daily_executed_notional(journal.recent(2), as_of)

    exec_agent = ExecutionAgent()
    results = exec_agent.execute(
        target,
        current_positions,
        cash,
        prices,
        dry_run=dry_run,
        orderbooks=orderbooks,
        adv_20d=adv_map,
        nav_today=nav,
        prev_nav=prev_nav,
        equity_curve=eq,
        executed_today_notional=today_executed,
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

    log.info("order.done", n_orders=len(results), dry_run=dry_run)
    return {
        "as_of": as_of.isoformat(),
        "n_orders": len(results),
        "execution_results": [r.model_dump(mode="json") for r in results],
    }


# ----------------------------------------------------------------------
# Intraday stops — fires every 30 min during regular hours.
# Cuts losers fast / locks in trailing profits, but **never adds or
# rebalances**. Stop selling only.
# ----------------------------------------------------------------------
def intraday_stops_phase(as_of: date | None = None) -> dict[str, Any]:
    """Re-evaluate per-name stops with live KIS prices and submit market SELLs.

    Steps:
        1. Pull live positions + prices from KIS.
        2. Bump persisted peak prices for every position seen at a new high.
        3. evaluate_stops → stop_orders → place_order for each.
        4. Journal + notify.

    Skipped silently when KIS credentials are missing (so dry-run dev boxes
    don't spam the broker). Always allowed to send sells, even when the
    broader pipeline is in dry-run mode — defending capital trumps the safety
    flag.
    """
    setup_logging()
    as_of = as_of or date.today()

    env = get_env()
    if not (env.kis_app_key and env.kis_app_secret):
        log.debug("intraday.no_kis_credentials")
        return {"as_of": as_of.isoformat(), "skipped": "no KIS credentials"}

    from broker.kis_client import KISClient
    from broker.tick_size import snap_to_tick
    from common.types import Side
    from portfolio.position_state import update_peak
    from portfolio.stops import (
        apply_state_after_fill,
        evaluate_stops,
        stop_orders,
    )

    client = KISClient()
    state = client.get_account_state()
    positions = state.get("positions") or {}
    if not positions:
        log.debug("intraday.no_positions")
        return {"as_of": as_of.isoformat(), "n_positions": 0, "n_stops": 0}

    prices = client.get_prices(list(positions.keys()))
    if not prices:
        log.warning("intraday.no_prices")
        return {"as_of": as_of.isoformat(), "n_positions": len(positions), "n_stops": 0}

    # Bump peak prices for trailing-stop reference (uses *intraday* highs).
    for ticker, px in prices.items():
        update_peak(ticker, px, as_of)

    signals = evaluate_stops(positions, prices)
    if not signals:
        log.info("intraday.no_stops_fired", n_positions=len(positions))
        return {"as_of": as_of.isoformat(), "n_positions": len(positions), "n_stops": 0}

    journal = DecisionJournal()
    notify_warning(
        "Intraday stops fired",
        f"{len(signals)} positions about to be sold",
        tickers=",".join(s.ticker for s in signals),
    )

    raw_orders = stop_orders(signals, prices)
    # Snap any limit prices (stop_orders emits market orders, but be defensive)
    submitted: list[dict[str, Any]] = []
    for order in raw_orders:
        if order.order_type == "limit":
            price = float(snap_to_tick(order.price or 0, side="up"))
            order = order.model_copy(update={"price": price})
        result = client.place_order(order)
        submitted.append(result.model_dump(mode="json"))
        journal.record(
            agent="execution",
            action=f"INTRADAY_STOP_{order.side.value}",
            ticker=order.ticker,
            conviction=10,
            rationale=result.message,
            context=result.model_dump(mode="json"),
        )
        if result.status == "rejected":
            notify_error(
                "Intraday stop order rejected",
                result.message,
                ticker=order.ticker,
                qty=order.quantity,
            )
        else:
            # Maintain position_state so the trailing peak resets on full exit
            apply_state_after_fill(
                order.ticker, Side.SELL, order.quantity, prices.get(order.ticker, 0.0), as_of
            )
    log.warning("intraday.stops_executed", n=len(submitted))
    return {
        "as_of": as_of.isoformat(),
        "n_positions": len(positions),
        "n_stops": len(submitted),
        "results": submitted,
    }


# ----------------------------------------------------------------------
# End-of-day mark-to-market
# ----------------------------------------------------------------------
def eod_phase(as_of: date | None = None) -> dict[str, Any]:
    setup_logging()
    as_of = as_of or date.today()
    log.info("eod.start", as_of=as_of.isoformat())

    env = get_env()
    nav = 0.0
    if env.kis_app_key and env.kis_app_secret:
        try:
            state = KISClient().get_account_state()
            nav = state["cash"] + sum(
                q * state["prices"].get(t, 0.0)
                for t, q in state["positions"].items()
            )
        except Exception as e:
            log.warning("eod.kis_unavailable", error=str(e))

    if nav > 0:
        persist_nav(as_of, nav)

    from memory.outcomes import update_outcomes

    counts = update_outcomes()
    log.info("eod.done", nav=nav, **counts)
    return {"as_of": as_of.isoformat(), "nav": nav, **counts}


# ----------------------------------------------------------------------
# Convenience: research + order in one go
# ----------------------------------------------------------------------
def run_daily(as_of: date | None = None, *, dry_run: bool = True) -> dict[str, Any]:
    res = research_phase(as_of)
    out = order_phase(as_of, dry_run=dry_run)
    return {"research": res, "order": out}


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
