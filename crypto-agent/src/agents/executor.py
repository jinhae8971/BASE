"""Executor (Trader) agent — turns aggregated signals into concrete orders."""

from __future__ import annotations

import json
from typing import Any

from src.agents.base import AgentContext, BaseAgent


class ExecutorAgent(BaseAgent):
    name = "executor"
    model_key = "strong"
    max_tokens = 4096  # needs room for order list + rationale

    @property
    def tool_name(self) -> str:
        return "emit_executor"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["target_weights", "cash_pct", "orders", "rationale"],
            "properties": {
                "target_weights": {
                    "type": "object",
                    "additionalProperties": {"type": "number", "minimum": 0, "maximum": 100},
                },
                "cash_pct": {"type": "number", "minimum": 0, "maximum": 100},
                "orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["symbol", "side", "qty_usd"],
                        "properties": {
                            "symbol": {"type": "string"},
                            "side": {"type": "string", "enum": ["BUY", "SELL"]},
                            "qty_usd": {"type": "number", "minimum": 0},
                            "type": {"type": "string", "enum": ["MARKET", "LIMIT"]},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "rationale": {"type": "string", "maxLength": 800},
            },
        }

    def user_message(self, ctx: AgentContext) -> str:
        return json.dumps(
            {
                "universe": ctx.universe,
                "portfolio": ctx.portfolio,
                "lessons": ctx.lessons[:5],
            },
            ensure_ascii=False,
        )

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
