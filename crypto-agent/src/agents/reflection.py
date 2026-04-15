"""Reflection agent -- post-mortem after each closed trade."""

from __future__ import annotations

import json
from typing import Any

from src.agents.base import AgentContext, BaseAgent

AGENT_KEYS = ["research", "macro", "sector", "value", "quant", "executor"]


class ReflectionAgent(BaseAgent):
    name = "reflection"
    model_key = "strong"
    max_tokens = 1024

    @property
    def tool_name(self) -> str:
        return "emit_reflection"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["lesson", "agent_scores", "tags"],
            "properties": {
                "lesson": {"type": "string", "minLength": 20, "maxLength": 800},
                "agent_scores": {
                    "type": "object",
                    "required": AGENT_KEYS,
                    "properties": {
                        k: {"type": "number", "minimum": -1, "maximum": 1}
                        for k in AGENT_KEYS
                    },
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 10,
                },
            },
        }

    def user_message(self, ctx: AgentContext) -> str:
        # Reflection reads trade metadata out of ctx.portfolio, which the
        # post-mortem pipeline populates from the closed TradeRecord.
        return json.dumps(ctx.portfolio, ensure_ascii=False)

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "lesson": "stub: no closed trades yet to reflect on",
            "agent_scores": {k: 0.0 for k in AGENT_KEYS},
            "tags": [],
        }

    def _validate(self, payload: dict[str, Any]) -> None:
        if "lesson" not in payload or not isinstance(payload["lesson"], str):
            raise ValueError("reflection: missing lesson string")
        scores = payload.get("agent_scores", {})
        for k in AGENT_KEYS:
            if k not in scores:
                raise ValueError(f"reflection: missing score for {k}")
            if not -1.0 <= float(scores[k]) <= 1.0:
                raise ValueError(f"reflection: {k} delta out of range")
