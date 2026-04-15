"""Executor (Trader) agent — turns signals into concrete Binance orders.

This is the one agent that actually decides *what to trade*. It reads:
  - aggregated signals from the other five agents,
  - the current portfolio snapshot,
  - the macro regime (for cash floor),
  - the risk guardrail state.

It must emit a list of orders that will be filtered again by `portfolio.risk`
before being sent to the Binance executor. In dry mode we return an empty
order list so wiring tests do not accidentally move capital.
"""

from __future__ import annotations

from typing import Any

from src.agents.base import AgentContext, BaseAgent


class ExecutorAgent(BaseAgent):
    name = "executor"
    model_key = "strong"  # Opus — final decision gate

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "target_weights": {sym: 0.0 for sym in ctx.universe},
            "cash_pct": 100.0,
            "orders": [],
            "rationale": "stub: dry mode — no orders emitted",
        }

    def _validate(self, payload: dict[str, Any]) -> None:
        for k in ("target_weights", "cash_pct", "orders", "rationale"):
            if k not in payload:
                raise ValueError(f"executor: missing {k}")
        weights = payload["target_weights"]
        if not isinstance(weights, dict):
            raise ValueError("executor: target_weights must be dict")
        total = sum(float(w) for w in weights.values()) + float(payload["cash_pct"])
        if not 99.0 <= total <= 101.0:
            raise ValueError(f"executor: weights + cash must sum ~100, got {total:.2f}")
        for order in payload["orders"]:
            for k in ("symbol", "side", "qty_usd"):
                if k not in order:
                    raise ValueError(f"executor: order missing {k}")
            if order["side"] not in {"BUY", "SELL"}:
                raise ValueError(f"executor: bad side {order['side']!r}")
