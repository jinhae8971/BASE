"""Macro agent — DXY, rates, equities, risk regime."""

from __future__ import annotations

import json
from typing import Any

from src.agents.base import AgentContext, BaseAgent


class MacroAgent(BaseAgent):
    name = "macro"
    model_key = "fast"

    @property
    def tool_name(self) -> str:
        return "emit_macro"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["regime", "btc_bias", "leverage_cap", "cash_floor_pct", "notes"],
            "properties": {
                "regime": {"type": "string", "enum": ["risk-on", "neutral", "risk-off"]},
                "btc_bias": {"type": "number", "minimum": -1, "maximum": 1},
                "leverage_cap": {"type": "number", "minimum": 0, "maximum": 1},
                "cash_floor_pct": {"type": "number", "minimum": 0, "maximum": 100},
                "notes": {"type": "string", "maxLength": 400},
            },
        }

    def user_message(self, ctx: AgentContext) -> str:
        return json.dumps(
            {
                "fred": ctx.macro_data.get("fred", {}),
                "as_of": ctx.macro_data.get("as_of", {}),
            }
        )

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "regime": "neutral",
            "btc_bias": 0.0,
            "leverage_cap": 1.0,
            "cash_floor_pct": 20.0,
            "notes": "stub: no macro data pulled yet",
        }

    def _validate(self, payload: dict[str, Any]) -> None:
        required = {"regime", "btc_bias", "leverage_cap", "cash_floor_pct"}
        if not required.issubset(payload):
            raise ValueError(f"macro: missing keys {required - set(payload)}")
        if payload["regime"] not in {"risk-on", "neutral", "risk-off"}:
            raise ValueError(f"macro: bad regime {payload['regime']!r}")
        if not -1.0 <= float(payload["btc_bias"]) <= 1.0:
            raise ValueError("macro: btc_bias out of range")
        if not 0.0 <= float(payload["leverage_cap"]) <= 1.0:
            raise ValueError("macro: leverage_cap out of range")
