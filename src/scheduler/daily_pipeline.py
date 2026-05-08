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
from common.config import get_env, get_setting
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

    from common.calendar import is_trading_day

    if not is_trading_day(as_of):
        log.info("order.skip_non_trading_day", as_of=as_of.isoformat())
        return {"as_of": as_of.isoformat(), "skipped": "non_trading_day"}

    loaded = state_store.load_target(as_of)
    if loaded is None:
        log.warning("order.no_saved_target_running_research")
        research_phase(as_of)
        loaded = state_store.load_target(as_of)
        if loaded is None:
            return {"as_of": as_of.isoformat(), "error": "no target available"}
    target, _extra = loaded

    # Pre-market shock gate — KOSPI proxy (EWY) overnight move.
    # When the breach fires we either skip new orders entirely or scale them
    # down, configured via ``execution.shock_action`` ("skip" or "scale").
    try:
        from data.macro import fetch_overnight_shock

        shock = fetch_overnight_shock()
        if shock.get("breached"):
            action = str(get_setting("execution.shock_action", "skip"))
            shock_pct = shock.get("shock_pct", 0.0)
            notify_warning(
                "Overnight shock — gate fired",
                f"EWY overnight {shock_pct:+.2%} <= {shock.get('threshold')}; "
                f"action={action}",
                proxy=shock.get("proxy"),
            )
            if action == "skip":
                log.warning("order.skip_due_to_shock", **shock)
                return {
                    "as_of": as_of.isoformat(),
                    "skipped": "overnight_shock",
                    "shock": shock,
                }
            # otherwise "scale": halve every target weight
            target = target.model_copy(
                update={
                    "positions": {t: w * 0.5 for t, w in target.positions.items()},
                    "rationale": (
                        f"{target.rationale} | overnight shock {shock_pct:+.2%} "
                        f"-> halving all targets"
                    ),
                }
            )
    except Exception as e:
        log.warning("order.shock_check_failed", error=str(e))

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
# Monitor unfilled — runs once a few minutes after order_phase.
# Cancels any limit order that hasn't filled and replaces it at the
# current bid/ask (so we don't sit with a stale limit while the stock
# runs away from us).
# ----------------------------------------------------------------------
def monitor_unfilled_phase(as_of: date | None = None) -> dict[str, Any]:
    setup_logging()
    as_of = as_of or date.today()
    log.info("monitor_unfilled.start", as_of=as_of.isoformat())

    from common.calendar import is_trading_day

    if not is_trading_day(as_of):
        return {"as_of": as_of.isoformat(), "skipped": "non_trading_day"}

    env = get_env()
    if not (env.kis_app_key and env.kis_app_secret):
        log.debug("monitor_unfilled.no_kis_credentials")
        return {"as_of": as_of.isoformat(), "skipped": "no KIS credentials"}

    from broker.kis_client import KISClient
    from broker.tick_size import snap_to_tick
    from common.types import Order, Side

    client = KISClient()
    unfilled = client.get_unfilled_orders()
    if not unfilled:
        log.info("monitor_unfilled.none")
        return {"as_of": as_of.isoformat(), "n_replaced": 0}

    journal = DecisionJournal()
    replaced: list[dict[str, Any]] = []
    for u in unfilled:
        ticker = u["ticker"]
        broker_id = u["broker_order_id"]
        remaining = u["remaining"]
        side = Side.BUY if u["side"] == "BUY" else Side.SELL
        if remaining <= 0 or not broker_id:
            continue

        # Pull a fresh quote and decide on the new price.
        ob = client.get_orderbook(ticker)
        last_price = client.get_price(ticker) if not (ob.get("bid") or ob.get("ask")) else 0.0
        if side is Side.BUY:
            ref = ob.get("ask") or last_price
            new_price = float(snap_to_tick(ref, side="down"))
        else:
            ref = ob.get("bid") or last_price
            new_price = float(snap_to_tick(ref, side="up"))
        if new_price <= 0 or abs(new_price - u["price"]) < 1e-6:
            # No meaningful price change — leave the order alone.
            continue

        # Cancel + re-submit
        cancel_resp = client.cancel_order(ticker, broker_id, remaining)
        if str(cancel_resp.get("rt_cd")) != "0":
            log.warning(
                "monitor_unfilled.cancel_failed",
                ticker=ticker,
                broker_id=broker_id,
                msg=cancel_resp.get("msg1"),
            )
            continue
        new_order = Order(
            ticker=ticker,
            side=side,
            quantity=remaining,
            price=new_price,
            order_type="limit",
        )
        result = client.place_order(new_order)
        replaced.append(
            {
                "ticker": ticker,
                "old_price": u["price"],
                "new_price": new_price,
                "remaining": remaining,
                "result": result.model_dump(mode="json"),
            }
        )
        journal.record(
            agent="execution",
            action=f"REPLACE_{side.value}",
            ticker=ticker,
            conviction=10,
            rationale=f"reprice {u['price']:.0f} -> {new_price:.0f}",
            context=result.model_dump(mode="json"),
        )

    if replaced:
        notify_warning(
            "Unfilled orders replaced",
            f"{len(replaced)} orders re-priced to fresh bid/ask",
            tickers=",".join(r["ticker"] for r in replaced),
        )
    log.info("monitor_unfilled.done", n_replaced=len(replaced))
    return {"as_of": as_of.isoformat(), "n_replaced": len(replaced), "replaced": replaced}


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

    from common.calendar import is_trading_day

    if not is_trading_day(as_of):
        return {"as_of": as_of.isoformat(), "skipped": "non_trading_day"}

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


def _reconcile_position_state_from_kis() -> None:
    """Sync the local position_state table with KIS truth.

    KIS balance carries the *actual* held qty + average buy price after every
    partial / full fill. Aligning to that source of truth means a partially
    filled BUY doesn't leave us with an incorrect entry_price (which would
    miscalibrate hard_stop and pyramid triggers).
    """
    from broker.kis_client import KISClient
    from portfolio.position_state import (
        PositionState,
        all_states,
        remove,
        upsert_on_buy,
    )

    state = KISClient().get_account_state()
    raw = state.get("raw") or {}
    truth: dict[str, dict[str, float]] = {}
    for row in raw.get("output1", []) or []:
        ticker = (row.get("pdno") or "").strip()
        if not ticker:
            continue
        try:
            qty = int(float(row.get("hldg_qty") or 0))
            avg = float(row.get("pchs_avg_pric") or 0)
        except ValueError:
            continue
        if qty <= 0 or avg <= 0:
            continue
        truth[ticker] = {"qty": qty, "avg": avg}

    persisted = {s.ticker: s for s in all_states()}

    # Drop persisted rows for tickers no longer held.
    for ticker in persisted.keys() - truth.keys():
        remove(ticker)

    # Upsert any disagreement (qty or avg) using the broker's numbers.
    today = date.today()
    for ticker, t in truth.items():
        local: PositionState | None = persisted.get(ticker)
        if local is None or local.qty != t["qty"] or abs(local.entry_price - t["avg"]) > 1.0:
            # Replace by deleting and re-creating with the broker's avg as entry.
            remove(ticker)
            upsert_on_buy(ticker, t["avg"], int(t["qty"]), today)


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

    # Reconcile partial fills against position_state so trailing peaks /
    # pyramid PnL use real average entry prices, not pre-fill estimates.
    if env.kis_app_key and env.kis_app_secret:
        try:
            _reconcile_position_state_from_kis()
        except Exception as e:
            log.warning("eod.reconcile_failed", error=str(e))

    from memory.outcomes import update_outcomes

    counts = update_outcomes()

    # Drop the universe cache so tomorrow's research_phase pulls a fresh
    # KOSPI200 snapshot (membership changes weekly, market caps daily).
    try:
        from data.universe import invalidate_universe_cache

        invalidate_universe_cache()
    except Exception as e:
        log.warning("eod.cache_invalidate_failed", error=str(e))

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
