"""Sector agent — L1/L2/DeFi/AI/RWA rotation."""

from __future__ import annotations

import json
from typing import Any

from src.agents.base import AgentContext, BaseAgent

SECTOR_KEYS = ["L1", "L2", "DeFi", "AI", "RWA", "Gaming", "Meme"]


class SectorAgent(BaseAgent):
    name = "sector"
    model_key = "fast"

    @property
    def tool_name(self) -> str:
        return "emit_sector"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["sector_scores", "hot_sectors", "rotation_signal"],
            "properties": {
                "sector_scores": {
                    "type": "object",
                    "required": SECTOR_KEYS,
                    "properties": {
                        k: {"type": "number", "minimum": -1, "maximum": 1}
                        for k in SECTOR_KEYS
                    },
                },
                "hot_sectors": {
                    "type": "array",
                    "items": {"type": "string", "enum": SECTOR_KEYS},
                    "maxItems": 3,
                },
                "rotation_signal": {
                    "type": "string",
                    "enum": ["into-majors", "into-alt-sectors", "none"],
                },
            },
        }

    def user_message(self, ctx: AgentContext) -> str:
        return json.dumps(
            {
                "categories": ctx.market_data.get("categories", []),
                "chain_tvl": ctx.onchain_data.get("chain_tvl", []),
            }
        )

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "sector_scores": {k: 0.0 for k in SECTOR_KEYS},
            "hot_sectors": [],
            "rotation_signal": "none",
        }

    def _validate(self, payload: dict[str, Any]) -> None:
        if "sector_scores" not in payload:
            raise ValueError("sector: missing sector_scores")
        for k in SECTOR_KEYS:
            if k not in payload["sector_scores"]:
                raise ValueError(f"sector: missing sector {k}")
            v = float(payload["sector_scores"][k])
            if not -1.0 <= v <= 1.0:
                raise ValueError(f"sector: {k} score out of range")
