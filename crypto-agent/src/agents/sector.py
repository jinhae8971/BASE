"""Sector agent — L1/L2/DeFi/AI/RWA rotation."""

from __future__ import annotations

from typing import Any

from src.agents.base import AgentContext, BaseAgent


class SectorAgent(BaseAgent):
    name = "sector"
    model_key = "fast"

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "sector_scores": {
                "L1": 0.0,
                "L2": 0.0,
                "DeFi": 0.0,
                "AI": 0.0,
                "RWA": 0.0,
                "Gaming": 0.0,
                "Meme": 0.0,
            },
            "hot_sectors": [],
            "rotation_signal": "none",
        }

    def _validate(self, payload: dict[str, Any]) -> None:
        if "sector_scores" not in payload:
            raise ValueError("sector: missing sector_scores")
        for sec, score in payload["sector_scores"].items():
            if not -1.0 <= float(score) <= 1.0:
                raise ValueError(f"sector: {sec} score out of range")
