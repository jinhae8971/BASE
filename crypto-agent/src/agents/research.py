"""Crypto Research agent — news, on-chain, narrative sentiment."""

from __future__ import annotations

from typing import Any

from src.agents.base import AgentContext, BaseAgent


class ResearchAgent(BaseAgent):
    name = "research"
    model_key = "fast"

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "coins": {
                sym: {
                    "sentiment": 0.0,          # -1..+1
                    "narrative_strength": 0.5, # 0..1
                    "risk_flags": [],
                }
                for sym in ctx.universe
            },
            "top_narratives": [],
        }

    def _validate(self, payload: dict[str, Any]) -> None:
        if "coins" not in payload or not isinstance(payload["coins"], dict):
            raise ValueError("research: missing 'coins' dict")
        for sym, row in payload["coins"].items():
            for k in ("sentiment", "narrative_strength", "risk_flags"):
                if k not in row:
                    raise ValueError(f"research: coin {sym} missing {k}")
            if not -1.0 <= float(row["sentiment"]) <= 1.0:
                raise ValueError(f"research: {sym} sentiment out of range")
