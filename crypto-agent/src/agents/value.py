"""Value Investor agent."""

from __future__ import annotations

import json
from typing import Any

from src.agents.base import AgentContext, BaseAgent


class ValueAgent(BaseAgent):
    name = "value"
    model_key = "strong"

    @property
    def tool_name(self) -> str:
        return "emit_value"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["coins"],
            "properties": {
                "coins": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "required": ["fair_value_ratio", "conviction", "horizon_days"],
                        "properties": {
                            "fair_value_ratio": {"type": "number", "minimum": 0},
                            "conviction": {"type": "number", "minimum": -1, "maximum": 1},
                            "horizon_days": {"type": "integer", "minimum": 1, "maximum": 720},
                        },
                    },
                }
            },
        }

    def user_message(self, ctx: AgentContext) -> str:
        return json.dumps(
            {
                "universe": ctx.universe,
                "markets": ctx.market_data.get("markets", []),
                "chain_tvl": ctx.onchain_data.get("chain_tvl", []),
                "lessons": ctx.lessons[:5],
            },
            ensure_ascii=False,
        )

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "coins": {
                sym: {
                    "fair_value_ratio": 1.0,
                    "conviction": 0.0,
                    "horizon_days": 90,
                }
                for sym in ctx.universe
            }
        }

    def _validate(self, payload: dict[str, Any]) -> None:
        if "coins" not in payload:
            raise ValueError("value: missing coins")
        for sym, row in payload["coins"].items():
            for k in ("fair_value_ratio", "conviction", "horizon_days"):
                if k not in row:
                    raise ValueError(f"value: {sym} missing {k}")
            if not -1.0 <= float(row["conviction"]) <= 1.0:
                raise ValueError(f"value: {sym} conviction out of range")
