"""Crypto Research agent."""

from __future__ import annotations

import json
from typing import Any

from src.agents.base import AgentContext, BaseAgent


class ResearchAgent(BaseAgent):
    name = "research"
    model_key = "fast"

    @property
    def tool_name(self) -> str:
        return "emit_research"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["coins", "top_narratives"],
            "properties": {
                "coins": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "required": ["sentiment", "narrative_strength", "risk_flags"],
                        "properties": {
                            "sentiment": {"type": "number", "minimum": -1, "maximum": 1},
                            "narrative_strength": {"type": "number", "minimum": 0, "maximum": 1},
                            "risk_flags": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "top_narratives": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 5,
                },
            },
        }

    def user_message(self, ctx: AgentContext) -> str:
        news_compact = [
            {
                "t": n.get("title", "")[:160],
                "cur": n.get("currencies", []),
                "v": n.get("votes", {}),
                "when": n.get("published_at", ""),
            }
            for n in ctx.news[:50]
        ]
        markets_compact = [
            {
                "s": m["symbol"],
                "mc": m["market_cap"],
                "vol": m["volume_24h"],
                "c24": m["chg24h"],
                "c7d": m["chg7d"],
            }
            for m in ctx.market_data.get("markets", [])[:50]
        ]
        return json.dumps(
            {
                "universe": ctx.universe,
                "news": news_compact,
                "markets": markets_compact,
                "lessons": ctx.lessons[:5],
            },
            ensure_ascii=False,
        )

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "coins": {
                sym: {
                    "sentiment": 0.0,
                    "narrative_strength": 0.5,
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
