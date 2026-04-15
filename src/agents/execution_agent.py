"""Execution agent — turns PortfolioTarget into KIS orders with risk guard."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from common.config import get_setting
from common.logging import get_logger
from common.types import (
    AgentProposal,
    ExecutionResult,
    Order,
    PortfolioTarget,
    Side,
)

from .base import BaseAgent

log = get_logger(__name__)


class ExecutionAgent(BaseAgent):
    """Execution agent.

    Unlike the specialists, this agent does NOT call an LLM for every decision
    path. Its primary job is deterministic risk-gating and order placement.
    It does, however, accept an optional LLM review pass for edge cases
    (e.g., "the target says SELL 100% of X — sanity check?").
    """

    name = "execution"
    prompt_file = "execution.md"

    def gather_context(self, as_of: date) -> dict[str, Any]:  # unused in default flow
        return {"as_of": as_of.isoformat()}

    def parse_response(self, text: str, as_of: date) -> AgentProposal:
        return AgentProposal(agent_name=self.name, as_of=as_of, conviction=0, rationale=text)

    # ------------------------------------------------------------------
    # Deterministic execution pipeline
    # ------------------------------------------------------------------
    def execute(
        self,
        target: PortfolioTarget,
        current_positions: dict[str, int],
        cash: float,
        prices: dict[str, float],
        *,
        dry_run: bool | None = None,
    ) -> list[ExecutionResult]:
        """Compute and (optionally) submit orders to reach `target` weights."""
        from broker.kis_client import KISClient

        if dry_run is None:
            dry_run = bool(get_setting("execution.dry_run_default", True))

        orders = self._plan_orders(target, current_positions, cash, prices)
        orders = self._apply_risk_guards(orders, target, cash, prices)

        client = KISClient()
        results: list[ExecutionResult] = []
        for order in orders:
            if dry_run:
                log.info("execution.dry_run", order=order.model_dump())
                results.append(
                    ExecutionResult(
                        order=order,
                        submitted_at=datetime.utcnow(),
                        status="submitted",
                        message="dry_run",
                    )
                )
            else:
                results.append(client.place_order(order))
        return results

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------
    def _plan_orders(
        self,
        target: PortfolioTarget,
        current_positions: dict[str, int],
        cash: float,
        prices: dict[str, float],
    ) -> list[Order]:
        nav = cash + sum(
            current_positions.get(t, 0) * prices.get(t, 0.0) for t in current_positions
        )
        rebalance_threshold = float(get_setting("risk.rebalance_threshold", 0.05))

        orders: list[Order] = []

        # SELL positions not in target or over target
        for ticker, qty in current_positions.items():
            price = prices.get(ticker)
            if not price or qty <= 0:
                continue
            target_w = target.positions.get(ticker, 0.0)
            target_qty = int((target_w * nav) // price)
            delta = target_qty - qty
            if abs(delta * price) / max(nav, 1.0) < rebalance_threshold:
                continue
            if delta < 0:
                orders.append(
                    Order(ticker=ticker, side=Side.SELL, quantity=-delta, price=price)
                )

        # BUY positions in target but under target
        for ticker, weight in target.positions.items():
            price = prices.get(ticker)
            if not price:
                continue
            target_qty = int((weight * nav) // price)
            current_qty = current_positions.get(ticker, 0)
            delta = target_qty - current_qty
            if delta > 0 and (delta * price) / max(nav, 1.0) >= rebalance_threshold:
                orders.append(
                    Order(ticker=ticker, side=Side.BUY, quantity=delta, price=price)
                )
        return orders

    # ------------------------------------------------------------------
    # Risk guards
    # ------------------------------------------------------------------
    def _apply_risk_guards(
        self,
        orders: list[Order],
        target: PortfolioTarget,
        cash: float,
        prices: dict[str, float],
    ) -> list[Order]:
        max_pos = float(get_setting("risk.max_position_weight", 0.10))
        for ticker, weight in target.positions.items():
            if weight > max_pos:
                log.warning(
                    "risk.position_over_limit",
                    ticker=ticker,
                    weight=weight,
                    limit=max_pos,
                )
        # Remove BUY orders that would exceed available cash
        buy_orders = [o for o in orders if o.side is Side.BUY]
        sell_orders = [o for o in orders if o.side is Side.SELL]
        buy_cost = sum((o.price or 0.0) * o.quantity for o in buy_orders)
        if buy_cost > cash:
            scale = cash / buy_cost if buy_cost else 0
            for o in buy_orders:
                o.quantity = int(o.quantity * scale)
            log.warning("risk.cash_constraint", scale=scale)
        return [o for o in (*sell_orders, *buy_orders) if o.quantity > 0]
