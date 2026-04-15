"""Reflection agent — post-mortem after each closed trade.

Runs asynchronously when a position closes. Compares each specialist agent's
predicted direction / conviction to realized P&L and writes:
  1. a natural-language `lesson` into the vector store (RAG for future runs),
  2. per-agent scoring deltas used by the ELO updater.
"""

from __future__ import annotations

from typing import Any

from src.agents.base import AgentContext, BaseAgent


class ReflectionAgent(BaseAgent):
    name = "reflection"
    model_key = "strong"

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "lesson": "stub: no closed trades yet",
            "agent_scores": {
                "research": 0.0,
                "macro": 0.0,
                "sector": 0.0,
                "value": 0.0,
                "quant": 0.0,
                "executor": 0.0,
            },
            "tags": [],
        }

    def _validate(self, payload: dict[str, Any]) -> None:
        if "lesson" not in payload or not isinstance(payload["lesson"], str):
            raise ValueError("reflection: missing lesson string")
        scores = payload.get("agent_scores", {})
        for agent, delta in scores.items():
            if not -1.0 <= float(delta) <= 1.0:
                raise ValueError(f"reflection: {agent} delta out of range")
