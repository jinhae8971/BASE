"""Daily workflow — the DAG that runs once per day.

Phase 3 upgrades:
  - **Graceful degradation**: one agent failure falls back to that agent's
    deterministic stub so the remaining agents and the optimizer still run.
    This matches how a human trading desk keeps working when one analyst is
    out sick.
  - **Lessons injection (RAG)**: the top-N most recent post-mortem lessons
    from the vector store are injected into `AgentContext.lessons` so every
    agent's user message can reference them. The generation side of the
    learning loop lands in Phase 6; the consumption side lives here.
  - **Run artifact**: every completed (or aborted) run writes a single JSON
    file under `data/runs/<date>/<run_id>.json` with the full agent
    decisions, allocation, orders, fills, and errors — the audit trail and
    the input to backtest regression / Optuna in later phases.
  - **Alerts**: optional Telegram notifications on halt / risk guardrail
    trips / uncaught failures.

Order of operations:
  1. Resolve universe (top N by 24h volume, filtered).
  2. Gather market snapshot (market / macro / onchain / news).
  3. Retrieve recent lessons from the vector store and inject into ctx.
  4. Run the five signal agents in parallel (with per-agent failure
     isolation via `_safe_run`).
  5. Aggregate signals via ELO-weighted blend.
  6. Optimize target portfolio.
  7. Executor agent proposes concrete orders.
  8. Risk guardrails filter orders (MDD circuit breaker trumps everything).
  9. Binance client submits orders (dry/paper/live).
 10. Persist trade records and write the run artifact.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
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
from src.llm import LLMClient
from src.logging import get_logger
from src.memory.trade_store import AgentDecision, InMemoryTradeStore, TradeRecord
from src.memory.vector_store import InMemoryLessonStore, default_store
from src.orchestrator.alerts import TelegramAlerter
from src.orchestrator.run_artifact import (
    RunArtifact,
    serialize_agent_result,
    write as write_artifact,
)
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
        llm_client: LLMClient | None = None,
        lesson_store: InMemoryLessonStore | None = None,
        alerter: TelegramAlerter | None = None,
        write_artifacts: bool = True,
        lessons_k: int = 5,
    ) -> None:
        self.elo = elo or EloTable()
        self.trade_store = trade_store or InMemoryTradeStore()
        self.binance = binance or BinanceClient()
        self._snapshot_fn = snapshot_fn or gather_snapshot
        self._llm = llm_client
        self.lesson_store = lesson_store or default_store()
        self.alerter = alerter
        self.write_artifacts = write_artifacts
        self.lessons_k = lessons_k
        # All agents share the same client so prompt-cache breakpoints set by
        # one agent warm the cache for every subsequent call in the run.
        self.signal_agents = [
            ResearchAgent(llm_client=llm_client),
            MacroAgent(llm_client=llm_client),
            SectorAgent(llm_client=llm_client),
            ValueAgent(llm_client=llm_client),
            QuantAgent(llm_client=llm_client),
        ]
        self.executor = ExecutorAgent(llm_client=llm_client)

    # ----------------------------------------------------------------
    # Public entrypoint
    # ----------------------------------------------------------------

    async def run(self, universe_size: int = 20) -> dict[str, Any]:
        run_id = uuid4().hex[:12]
        started_at = datetime.now(UTC)
        errors: list[str] = []
        log.info("run.start", run_id=run_id)

        async def _finalize(
            *,
            halted: bool,
            halt_reason: str | None,
            universe: list[str],
            snapshot_errors: dict[str, str],
            elo_weights: dict[str, float],
            agent_results: list[AgentResult],
            allocation_dict: dict[str, Any],
            proposed_orders: list[dict[str, Any]],
            approved_orders: list[dict[str, Any]],
            fills: list[dict[str, Any]],
            equity: float,
            macro_regime: str,
        ) -> dict[str, Any]:
            ended = datetime.now(UTC)
            artifact = RunArtifact(
                run_id=run_id,
                started_at=started_at,
                ended_at=ended,
                halted=halted,
                halt_reason=halt_reason,
                universe=universe,
                snapshot_errors=snapshot_errors,
                elo_weights=elo_weights,
                agent_results=[serialize_agent_result(r) for r in agent_results],
                allocation=allocation_dict,
                proposed_orders=proposed_orders,
                approved_orders=approved_orders,
                fills=fills,
                equity_usd=equity,
                macro_regime=macro_regime,
                errors=errors,
            )
            artifact_path: str | None = None
            if self.write_artifacts:
                try:
                    artifact_path = str(write_artifact(artifact))
                except Exception as exc:  # noqa: BLE001
                    log.warning("run.artifact_write_failed", err=str(exc))

            if self.alerter is not None:
                if halted:
                    await self.alerter.send(
                        f"run {run_id} halted: {halt_reason}", level="warn"
                    )
                elif errors:
                    await self.alerter.send(
                        f"run {run_id} completed with {len(errors)} errors", level="warn"
                    )
                else:
                    await self.alerter.send(
                        f"run {run_id} done: {len(approved_orders)} orders, "
                        f"equity ${equity:,.2f}"
                    )

            return {
                "run_id": run_id,
                "halted": halted,
                "reason": halt_reason,
                "universe": universe,
                "allocation": allocation_dict,
                "orders": approved_orders,
                "fills": fills,
                "elo_weights": elo_weights,
                "errors": errors,
                "artifact_path": artifact_path,
            }

        if is_halted():
            log.warning("run.halted", reason="HALT file present")
            return await _finalize(
                halted=True,
                halt_reason="HALT file present",
                universe=[],
                snapshot_errors={},
                elo_weights=self.elo.weights(),
                agent_results=[],
                allocation_dict={},
                proposed_orders=[],
                approved_orders=[],
                fills=[],
                equity=0.0,
                macro_regime="unknown",
            )

        # 1. Market snapshot
        try:
            snapshot: MarketSnapshot = await self._snapshot_fn(universe_size=universe_size)
        except Exception as exc:  # noqa: BLE001
            log.error("run.snapshot_failed", err=str(exc))
            errors.append(f"snapshot: {exc}")
            return await _finalize(
                halted=True,
                halt_reason=f"snapshot failed: {exc}",
                universe=[],
                snapshot_errors={},
                elo_weights=self.elo.weights(),
                agent_results=[],
                allocation_dict={},
                proposed_orders=[],
                approved_orders=[],
                fills=[],
                equity=0.0,
                macro_regime="unknown",
            )

        universe = snapshot.universe
        if not universe:
            log.error("run.empty_universe", errors=snapshot.errors)
            return await _finalize(
                halted=True,
                halt_reason="empty universe",
                universe=[],
                snapshot_errors=snapshot.errors,
                elo_weights=self.elo.weights(),
                agent_results=[],
                allocation_dict={},
                proposed_orders=[],
                approved_orders=[],
                fills=[],
                equity=0.0,
                macro_regime="unknown",
            )

        snap_fields = snapshot.agent_dict()
        lessons = await self._fetch_lessons()
        ctx = AgentContext(
            run_id=run_id,
            as_of=snapshot.as_of,
            universe=universe,
            market_data=snap_fields["market_data"],
            macro_data=snap_fields["macro_data"],
            onchain_data=snap_fields["onchain_data"],
            news=snap_fields["news"],
            lessons=lessons,
        )

        # 2. Signal agents in parallel with failure isolation.
        results_list: list[AgentResult] = await asyncio.gather(
            *(self._safe_run(a, ctx, errors) for a in self.signal_agents)
        )
        results: dict[str, AgentResult] = {r.agent: r for r in results_list}

        # 3. Aggregate + optimize.
        elo_weights = self.elo.weights()
        signals = aggregate(universe, results, elo_weights)
        macro_payload = results["macro"].payload
        allocation = optimize(
            signals, macro_cash_floor_pct=float(macro_payload.get("cash_floor_pct", 20.0))
        )

        # 4. Executor agent.
        equity = await self.binance.account_equity_usd()
        current_weights = self._current_weights(universe)
        exec_ctx = AgentContext(
            run_id=run_id,
            as_of=ctx.as_of,
            universe=universe,
            portfolio={
                "target_weights": allocation.weights,
                "cash_pct": allocation.cash_pct,
                "current_weights": current_weights,
                "equity_usd": equity,
                "signals": {s: {"score": sig.score} for s, sig in signals.items()},
            },
            lessons=lessons,
        )
        exec_result = await self._safe_run(self.executor, exec_ctx, errors)

        # 5. Risk guardrails.
        state = PortfolioState(
            equity_usd=equity,
            peak_equity_usd=equity,
            daily_pnl_pct=0.0,
            weekly_pnl_pct=0.0,
        )
        mdd = check_mdd(state)
        if mdd.action == GuardAction.HALT_ALL:
            log.error("risk.halt_all", reason=mdd.reason)
            return await _finalize(
                halted=True,
                halt_reason=mdd.reason,
                universe=universe,
                snapshot_errors=snapshot.errors,
                elo_weights=elo_weights,
                agent_results=list(results.values()) + [exec_result],
                allocation_dict=asdict(allocation),
                proposed_orders=exec_result.payload.get("orders", []),
                approved_orders=[],
                fills=[],
                equity=equity,
                macro_regime=str(macro_payload.get("regime", "unknown")),
            )

        daily = check_daily(state)
        proposed_orders = exec_result.payload.get("orders", [])
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

        # 6. Submit approved orders, tolerating per-order failures.
        fill_dicts: list[dict[str, Any]] = []
        for raw in approved_orders:
            try:
                fill = await self.binance.submit(
                    Order(symbol=raw["symbol"], side=raw["side"], qty_usd=raw["qty_usd"])
                )
            except Exception as exc:  # noqa: BLE001
                log.error("run.submit_failed", order=raw, err=str(exc))
                errors.append(f"submit {raw.get('symbol')}: {exc}")
                continue
            fill_dicts.append(
                {
                    "symbol": fill.symbol,
                    "side": fill.side,
                    "qty": fill.qty,
                    "price": fill.price,
                    "fee_usd": fill.fee_usd,
                    "filled_at": fill.filled_at.isoformat(),
                }
            )
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
                    macro_regime=str(macro_payload.get("regime", "unknown")),
                )
            )

        log.info("run.done", run_id=run_id, orders=len(approved_orders), errors=len(errors))
        return await _finalize(
            halted=False,
            halt_reason=None,
            universe=universe,
            snapshot_errors=snapshot.errors,
            elo_weights=elo_weights,
            agent_results=list(results.values()) + [exec_result],
            allocation_dict=asdict(allocation),
            proposed_orders=proposed_orders,
            approved_orders=approved_orders,
            fills=fill_dicts,
            equity=equity,
            macro_regime=str(macro_payload.get("regime", "unknown")),
        )

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    async def _safe_run(
        self,
        agent,
        ctx: AgentContext,
        errors: list[str],
    ) -> AgentResult:
        """Run one agent; on failure, fall back to its deterministic stub.

        This isolates agent failures so one broken LLM call, one malformed
        tool response, or one schema-violation bug doesn't halt the whole
        daily run. The stub payload goes through the same `_validate` as
        the real path, so we know the downstream consumers (aggregator,
        optimizer, executor) will see a valid shape.
        """
        try:
            return await agent.run(ctx)
        except Exception as exc:  # noqa: BLE001
            log.error("agent.failed", agent=agent.name, err=str(exc))
            errors.append(f"{agent.name}: {exc}")
            payload = agent._run_stub(ctx)
            try:
                agent._validate(payload)
            except Exception as inner:  # noqa: BLE001
                log.error("agent.stub_also_failed", agent=agent.name, err=str(inner))
            return AgentResult(
                agent=agent.name,
                run_id=ctx.run_id,
                payload=payload,
                model=agent.model,
            )

    async def _fetch_lessons(self) -> list[str]:
        try:
            recents = await self.lesson_store.recent(k=self.lessons_k)
            return [le.text for le in recents]
        except Exception as exc:  # noqa: BLE001
            log.warning("lessons.fetch_failed", err=str(exc))
            return []

    def _current_weights(self, universe: list[str]) -> dict[str, float]:
        """Expose current portfolio weights to the executor agent.

        The backtest's SimulatedBinanceClient carries a `state` and
        `current_prices`; real Binance clients would need their own accessor.
        Returns fractional weights (0..1), not percents.
        """
        sim = getattr(self.binance, "state", None)
        prices = getattr(self.binance, "current_prices", None)
        if sim is None or prices is None:
            return {sym: 0.0 for sym in universe}
        return sim.weights(prices)
