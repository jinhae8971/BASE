"""Execution agent — turns PortfolioTarget into KIS orders with risk guards.

Pipeline:
    1. Plan delta orders against the current portfolio.
    2. Apply per-name and cash-availability constraints.
    3. Apply DailyRiskGuard (loss kill / MDD trigger / turnover cap).
    4. Snap limit prices to KRX tick size, using bid/ask when supplied.
    5. Cap order quantity to a fraction of the 20-day ADV (liquidity guard).
    6. (Optional) LLM sanity-check via ``execution.md`` when conditions are
       suspicious (huge turnover, single-name spike).
    7. Submit (or dry-run) and return ExecutionResult per order.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from common.config import get_setting
from common.llm import call_claude
from common.logging import get_logger
from common.notifications import notify_error, notify_info, notify_warning
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
    """Risk-gated order planner.

    The LLM is consulted only as a *sanity check* on the deterministic plan,
    never as the originator. This keeps execution behaviour predictable.
    """

    name = "execution"
    prompt_file = "execution.md"
    use_rag = False

    def gather_context(self, as_of: date) -> dict[str, Any]:
        return {"as_of": as_of.isoformat()}

    def parse_response(self, text: str, as_of: date) -> AgentProposal:
        return AgentProposal(agent_name=self.name, as_of=as_of, conviction=0, rationale=text)

    # ------------------------------------------------------------------
    # Pipeline entry point
    # ------------------------------------------------------------------
    def execute(
        self,
        target: PortfolioTarget,
        current_positions: dict[str, int],
        cash: float,
        prices: dict[str, float],
        *,
        dry_run: bool | None = None,
        orderbooks: dict[str, dict[str, Any]] | None = None,
        adv_20d: dict[str, float] | None = None,
        nav_today: float | None = None,
        prev_nav: float | None = None,
        equity_curve: Any = None,
        executed_today_notional: float = 0.0,
        twap_interval_override: int | None = None,
    ) -> list[ExecutionResult]:
        from broker.kis_client import KISClient
        from portfolio.risk_guards import DailyRiskGuard

        if dry_run is None:
            dry_run = bool(get_setting("execution.dry_run_default", True))

        from portfolio.stops import (
            apply_state_after_fill,
            evaluate_stops,
            stop_orders,
        )

        nav = nav_today or (
            cash + sum(current_positions.get(t, 0) * prices.get(t, 0.0) for t in current_positions)
        )

        # 0. Per-name stops (fired BEFORE rebalance — let losses cut fast).
        forced_sells: list[Order] = []
        try:
            signals = evaluate_stops(current_positions, prices)
            forced_sells = stop_orders(signals, prices)
            if forced_sells:
                # Drop those tickers from the rebalance target so we don't
                # immediately buy back what the stop just sold.
                for o in forced_sells:
                    target.positions.pop(o.ticker, None)
                notify_warning(
                    "Stop loss / trailing fired",
                    f"{len(forced_sells)} positions stopped out",
                    tickers=",".join(o.ticker for o in forced_sells),
                )
        except Exception as e:
            log.warning("execution.stops_eval_failed", error=str(e))

        # 0b. Pyramid winners — bump target weight on +20%/+40% names.
        pyramid_intents: list = []
        try:
            from portfolio.pyramid import evaluate_pyramid

            pyramid_intents = evaluate_pyramid(target, current_positions, prices, nav)
            if pyramid_intents:
                notify_info(
                    "Pyramid intents",
                    f"{len(pyramid_intents)} winners scaled up",
                    tickers=",".join(p.ticker for p in pyramid_intents),
                )
        except Exception as e:
            log.warning("execution.pyramid_eval_failed", error=str(e))

        # 1-2. Plan + cash/per-name caps
        orders = self._plan_orders(target, current_positions, cash, prices)
        orders = self._apply_basic_caps(orders, target, cash)
        # Stops are added back at the front so they execute first.
        orders = [*forced_sells, *orders]

        # 3. Daily portfolio guards
        guard = DailyRiskGuard(
            prev_nav=prev_nav,
            equity_curve=equity_curve,
            executed_today_notional=executed_today_notional,
        )
        new_notional = sum((o.price or 0) * o.quantity for o in orders)
        decision = guard.evaluate(target, nav, new_notional)
        if decision.target_override is not None:
            target = decision.target_override
            # Re-plan against the de-leveraged target
            orders = self._plan_orders(target, current_positions, cash, prices)
            orders = self._apply_basic_caps(orders, target, cash)
        orders = guard.apply_to_orders(orders, decision)
        if decision.triggered:
            notify_warning(
                "Risk guard fired",
                ", ".join(decision.triggered),
                date=str(target.as_of),
                action=decision.reason,
            )

        # 4. Snap limit prices to KRX tick size, using bid/ask when present
        orders = [
            self._set_limit_price(o, prices, orderbooks or {}) for o in orders
        ]

        # 5. ADV liquidity cap
        if adv_20d:
            orders = self._cap_to_adv(orders, adv_20d)

        # 6. Optional LLM sanity-check
        if self._should_consult_llm(target, orders, nav):
            review = self._llm_review(target, orders, nav)
            if review.get("approved") is False:
                notify_warning(
                    "Execution LLM blocked",
                    review.get("rationale", ""),
                    warnings=", ".join(review.get("warnings", [])),
                )
                log.warning("execution.llm_blocked", review=review)
                return []
            for adj in review.get("adjustments") or []:
                tkr, new_w = adj.get("ticker"), adj.get("new_weight")
                if tkr and new_w is not None:
                    target.positions[tkr] = float(new_w)
            if review.get("adjustments"):
                orders = self._plan_orders(target, current_positions, cash, prices)
                orders = self._apply_basic_caps(orders, target, cash)
                orders = [
                    self._set_limit_price(o, prices, orderbooks or {}) for o in orders
                ]

        # 7. Submit (with optional TWAP slicing).
        # TWAP space child orders by execution.twap_interval_seconds. We
        # sleep in-process so each slice gets a fresh price/orderbook print
        # — caller is responsible for budgeting the order_phase wall clock.
        twap_enabled = bool(get_setting("execution.twap_enabled", False))
        twap_slices = max(1, int(get_setting("execution.twap_slices", 4)))
        # Bandit may override the TWAP interval — daily-tuned by ε-greedy
        twap_interval = (
            int(twap_interval_override)
            if twap_interval_override is not None
            else max(0, int(get_setting("execution.twap_interval_seconds", 0)))
        )
        sliced_orders = (
            self._twap_slice(orders, twap_slices) if twap_enabled else orders
        )

        client = KISClient()
        results: list[ExecutionResult] = []
        today_dt = datetime.utcnow().date()
        for slice_idx, order in enumerate(sliced_orders):
            # Space TWAP children. Skip the wait for slice 0 and dry-run.
            if (
                twap_enabled
                and not dry_run
                and twap_interval > 0
                and slice_idx > 0
                and slice_idx % twap_slices != 0
            ):
                self._twap_wait(twap_interval)
            if dry_run:
                log.info("execution.dry_run", order=order.model_dump())
                result = ExecutionResult(
                    order=order,
                    submitted_at=datetime.utcnow(),
                    status="submitted",
                    message="dry_run",
                )
            else:
                result = client.place_order(order)
                if result.status == "rejected":
                    notify_error(
                        "Order rejected",
                        result.message,
                        ticker=order.ticker,
                        side=order.side.value,
                        qty=order.quantity,
                    )
                else:
                    notify_info(
                        "Order submitted",
                        f"{order.side.value} {order.ticker} x {order.quantity}",
                        price=order.price,
                        broker_id=result.broker_order_id or "",
                    )
            results.append(result)

            # Maintain position_state — covers dry-run too so backtests/paper
            # exercise the stop logic end-to-end.
            if result.status in ("submitted", "filled", "partial"):
                fill_qty = (
                    result.filled_qty
                    if result.filled_qty
                    else order.quantity
                )
                fill_price = result.avg_price or (order.price or 0.0)
                if fill_qty > 0 and fill_price > 0:
                    apply_state_after_fill(
                        order.ticker, order.side, fill_qty, fill_price, today_dt
                    )

        # Commit pyramid level counters AFTER orders are submitted, so a
        # single +20% trigger doesn't fire twice across runs.
        if pyramid_intents:
            try:
                from portfolio.pyramid import commit_pyramid_levels

                commit_pyramid_levels(pyramid_intents)
            except Exception as e:
                log.warning("execution.pyramid_commit_failed", error=str(e))
        return results

    # ------------------------------------------------------------------
    def _twap_slice(self, orders: list[Order], slices: int) -> list[Order]:
        """Split each order into ``slices`` roughly-equal child orders.

        Slice ``i`` of order ``o`` is emitted at index ``slices * o + i``
        in the output list — the submit loop above uses that ordering to
        space children with ``execution.twap_interval_seconds``.
        """
        if slices <= 1:
            return orders
        out: list[Order] = []
        for o in orders:
            chunk = max(1, o.quantity // slices)
            remaining = o.quantity
            for i in range(slices):
                qty = chunk if i < slices - 1 else remaining
                if qty <= 0:
                    break
                out.append(o.model_copy(update={"quantity": qty}))
                remaining -= qty
        return out

    @staticmethod
    def _twap_wait(seconds: int) -> None:
        """Hook for tests to monkey-patch. Real impl just sleeps."""
        import time

        time.sleep(seconds)

    # ------------------------------------------------------------------
    # Order planning
    # ------------------------------------------------------------------
    def _plan_orders(
        self,
        target: PortfolioTarget,
        current_positions: dict[str, int],
        cash: float,
        prices: dict[str, float],
    ) -> list[Order]:
        """Plan delta orders with **asymmetric** rebalance thresholds.

        Default behaviour ('let winners run'):
            BUY  fires when drift >= execution.rebalance_buy_threshold  (4%)
            SELL fires when drift >= execution.rebalance_sell_threshold (8%)

        We are *quicker to add* and *slower to trim* — keeping winners
        in the book through normal volatility.
        """
        nav = cash + sum(
            current_positions.get(t, 0) * prices.get(t, 0.0) for t in current_positions
        )
        # Backwards-compatible fallback to the symmetric setting
        sym = float(get_setting("risk.rebalance_threshold", 0.05))
        buy_thr = float(get_setting("execution.rebalance_buy_threshold", sym))
        sell_thr = float(get_setting("execution.rebalance_sell_threshold", sym * 1.6))

        orders: list[Order] = []

        for ticker, qty in current_positions.items():
            price = prices.get(ticker)
            if not price or qty <= 0:
                continue
            target_w = target.positions.get(ticker, 0.0)
            target_qty = int((target_w * nav) // price)
            delta = target_qty - qty
            if delta >= 0:
                continue
            drift = abs(delta * price) / max(nav, 1.0)
            # Always exit if the target dropped to zero (no position) — that's
            # an active SELL, not a drift trim.
            if target_w == 0.0 or drift >= sell_thr:
                orders.append(
                    Order(ticker=ticker, side=Side.SELL, quantity=-delta, price=price)
                )

        for ticker, weight in target.positions.items():
            price = prices.get(ticker)
            if not price:
                log.warning("execution.no_price", ticker=ticker)
                continue
            target_qty = int((weight * nav) // price)
            current_qty = current_positions.get(ticker, 0)
            delta = target_qty - current_qty
            if delta > 0 and (delta * price) / max(nav, 1.0) >= buy_thr:
                orders.append(
                    Order(ticker=ticker, side=Side.BUY, quantity=delta, price=price)
                )
        return orders

    # ------------------------------------------------------------------
    def _apply_basic_caps(
        self,
        orders: list[Order],
        target: PortfolioTarget,
        cash: float,
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
        buy_orders = [o for o in orders if o.side is Side.BUY]
        sell_orders = [o for o in orders if o.side is Side.SELL]
        buy_cost = sum((o.price or 0.0) * o.quantity for o in buy_orders)
        if buy_cost > cash and buy_cost > 0:
            scale = cash / buy_cost
            for o in buy_orders:
                o.quantity = int(o.quantity * scale)
            log.warning("risk.cash_constraint", scale=scale)
        return [o for o in (*sell_orders, *buy_orders) if o.quantity > 0]

    # ------------------------------------------------------------------
    def _set_limit_price(
        self,
        order: Order,
        prices: dict[str, float],
        orderbooks: dict[str, dict[str, Any]],
    ) -> Order:
        """Pick a quote-aware limit price.

        Buys: use the current ask (likely to fill); fall back to last + 0.3%.
        Sells: use the current bid; fall back to last - 0.3%.
        """
        from broker.tick_size import snap_to_tick

        ob = orderbooks.get(order.ticker, {})
        last = prices.get(order.ticker, order.price or 0.0)
        offset = float(get_setting("execution.limit_offset_bps", 30)) / 10_000.0

        if order.side is Side.BUY:
            ref = ob.get("ask") or last * (1 + offset)
            snapped = snap_to_tick(ref, side="down")
        else:
            ref = ob.get("bid") or last * (1 - offset)
            snapped = snap_to_tick(ref, side="up")
        return order.model_copy(update={"price": float(snapped)})

    # ------------------------------------------------------------------
    def _cap_to_adv(
        self, orders: list[Order], adv_20d: dict[str, float]
    ) -> list[Order]:
        """Don't trade more than ``execution.max_pct_of_adv`` of 20d ADV."""
        max_pct = float(get_setting("execution.max_pct_of_adv", 0.10))
        adjusted: list[Order] = []
        for o in orders:
            adv = adv_20d.get(o.ticker, 0.0)
            if adv <= 0:
                adjusted.append(o)
                continue
            max_notional = adv * max_pct
            cur_notional = (o.price or 0) * o.quantity
            if cur_notional <= max_notional:
                adjusted.append(o)
                continue
            if o.price and o.price > 0:
                new_qty = int(max_notional // o.price)
                if new_qty > 0:
                    log.warning(
                        "execution.adv_cap",
                        ticker=o.ticker,
                        old_qty=o.quantity,
                        new_qty=new_qty,
                        adv=adv,
                    )
                    adjusted.append(o.model_copy(update={"quantity": new_qty}))
        return adjusted

    # ------------------------------------------------------------------
    def _should_consult_llm(
        self, target: PortfolioTarget, orders: list[Order], nav: float
    ) -> bool:
        """LLM review is opt-in to keep latency / cost down.

        We trigger when:
            - turnover is unusually large (>15% of NAV)
            - a single position dominates target (>=8%)
        """
        if not orders or nav <= 0:
            return False
        notional = sum((o.price or 0) * o.quantity for o in orders)
        if notional / nav > 0.15:
            return True
        return any(w >= 0.08 for w in target.positions.values())

    def _llm_review(
        self, target: PortfolioTarget, orders: list[Order], nav: float
    ) -> dict[str, Any]:
        try:
            ctx = {
                "target_positions": target.positions,
                "cash_weight": target.cash_weight,
                "nav": nav,
                "orders": [
                    {
                        "ticker": o.ticker,
                        "side": o.side.value,
                        "qty": o.quantity,
                        "price": o.price,
                    }
                    for o in orders
                ],
            }
            system = self._load_system_prompt()
            raw = call_claude(
                system=system,
                messages=[{"role": "user", "content": str(ctx)}],
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            return self._extract_json(raw)
        except Exception as e:
            log.warning("execution.llm_review_failed", error=str(e))
            # On failure, default to "approved" so we don't block the day.
            return {"approved": True, "warnings": [], "adjustments": []}
