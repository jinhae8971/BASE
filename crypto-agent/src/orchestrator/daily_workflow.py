"""Daily workflow — the DAG that runs once per day.

Phase 0 wires everything in pure Python so we can unit-test the full
end-to-end flow without Postgres, Qdrant, or Anthropic. Phase 2 will move
this onto LangGraph with explicit nodes and state checkpoints.

Order of operations:
  1. Resolve universe (top N by 24h volume, filtered).
  2. Gather data snapshot (market / macro / onchain / news).
  3. Run the five signal agents in parallel.
  4. Aggregate signals via ELO-weighted blend.
  5. Optimize target portfolio.
  6. Executor agent proposes concrete orders.
  7. Risk guardrails filter orders.
  8. Binance client submits orders (dry/paper/live).
  9. Persist everything to the trade journal.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from src.agents import (
    AgentContext,
    ExecutorAgent,
    MacroAgent,
    QuantAgent,
    ResearchAgent,
    SectorAgent,
    ValueAgent,
)
from src.agents.base import AgentResult
from src.data.snapshot import MarketSnapshot, gather as gather_snapshot
from src.execution.binance_client import BinanceClient, Order
from src.execution.killswitch import is_halted
from src.learning.elo import EloTable
from src.logging import get_logger
from src.memory.trade_store import AgentDecision, InMemoryTradeStore, TradeRecord
from src.portfolio.aggregator import aggregate
from src.portfolio.optimizer import optimize
from src.portfolio.risk import (
    GuardAction,
    PortfolioState,
    check_daily,
    check_mdd,
    check_order_sanity,
)

log = get_logger("orchestrator")


class DailyWorkflow:
    def __init__(
        self,
        elo: EloTable | None = None,
        trade_store: InMemoryTradeStore | None = None,
        binance: BinanceClient | None = None,
        snapshot_fn=None,
    ) -> None:
        self.elo = elo or EloTable()
        self.trade_store = trade_store or InMemoryTradeStore()
        self.binance = binance or BinanceClient()
        # Injectable for tests; defaults to the real MarketSnapshot fanout.
        self._snapshot_fn = snapshot_fn or gather_snapshot
        self.signal_agents = [
            ResearchAgent(),
            MacroAgent(),
            SectorAgent(),
            ValueAgent(),
            QuantAgent(),
        ]
        self.executor = ExecutorAgent()

    async def run(self, universe_size: int = 20) -> dict[str, Any]:
        run_id = uuid4().hex[:12]
        log.info("run.start", run_id=run_id)

        if is_halted():
            log.warning("run.halted")
            return {"run_id": run_id, "halted": True}

        # 1. Collect one market snapshot and build the shared agent context.
        snapshot: MarketSnapshot = await self._snapshot_fn(universe_size=universe_size)
        universe = snapshot.universe
        if not universe:
            log.error("run.empty_universe", errors=snapshot.errors)
            return {"run_id": run_id, "halted": True, "reason": "empty universe", "errors": snapshot.errors}

        snap_fields = snapshot.agent_dict()
        ctx = AgentContext(
            run_id=run_id,
            as_of=snapshot.as_of,
            universe=universe,
            market_data=snap_fields["market_data"],
            macro_data=snap_fields["macro_data"],
            onchain_data=snap_fields["onchain_data"],
            news=snap_fields["news"],
        )

        # 3. Signal agents in parallel
        results_list: list[AgentResult] = await asyncio.gather(
            *(a.run(ctx) for a in self.signal_agents)
        )
        results: dict[str, AgentResult] = {r.agent: r for r in results_list}

        # 4. Aggregate
        weights = self.elo.weights()
        signals = aggregate(universe, results, weights)

        # 5. Optimize
        macro_payload = results["macro"].payload
        allocation = optimize(signals, macro_cash_floor_pct=macro_payload["cash_floor_pct"])

        # 6. Executor agent
        exec_ctx = AgentContext(
            run_id=run_id,
            as_of=ctx.as_of,
            universe=universe,
            portfolio={
                "target_weights": allocation.weights,
                "cash_pct": allocation.cash_pct,
                "signals": {s: {"score": sig.score} for s, sig in signals.items()},
            },
        )
        exec_result = await self.executor.run(exec_ctx)

        # 7. Risk filter
        equity = await self.binance.account_equity_usd()
        state = PortfolioState(
            equity_usd=equity,
            peak_equity_usd=equity,
            daily_pnl_pct=0.0,
            weekly_pnl_pct=0.0,
        )
        mdd = check_mdd(state)
        if mdd.action == GuardAction.HALT_ALL:
            log.error("risk.halt_all", reason=mdd.reason)
            return {"run_id": run_id, "halted": True, "reason": mdd.reason}

        daily = check_daily(state)
        proposed_orders = exec_result.payload["orders"]
        approved_orders: list[dict[str, Any]] = []
        if daily.action == GuardAction.ALLOW:
            for raw in proposed_orders:
                sanity = check_order_sanity(raw["qty_usd"], equity)
                if sanity.action == GuardAction.SKIP:
                    log.warning("risk.skip", order=raw, reason=sanity.reason)
                    continue
                if sanity.action == GuardAction.DOWNSIZE:
                    raw = {**raw, "qty_usd": sanity.new_qty_usd}
                approved_orders.append(raw)

        # 8. Submit
        fills = []
        for raw in approved_orders:
            fill = await self.binance.submit(
                Order(symbol=raw["symbol"], side=raw["side"], qty_usd=raw["qty_usd"])
            )
            fills.append(fill)
            await self.trade_store.insert(
                TradeRecord(
                    run_id=run_id,
                    ts=fill.filled_at,
                    symbol=fill.symbol,
                    side=fill.side,
                    qty=fill.qty,
                    price=fill.price,
                    notional_usd=raw["qty_usd"],
                    agent_decisions=[
                        AgentDecision(
                            agent=r.agent,
                            model=r.model,
                            payload=r.payload,
                            latency_ms=r.latency_ms,
                        )
                        for r in list(results.values()) + [exec_result]
                    ],
                    portfolio_snapshot={
                        "target_weights": allocation.weights,
                        "cash_pct": allocation.cash_pct,
                    },
                    macro_regime=macro_payload["regime"],
                )
            )

        log.info("run.done", run_id=run_id, orders=len(approved_orders))
        return {
            "run_id": run_id,
            "universe": universe,
            "allocation": asdict(allocation),
            "orders": approved_orders,
            "fills": [f.__dict__ for f in fills],
            "elo_weights": weights,
        }
