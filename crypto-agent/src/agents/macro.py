"""Macro agent — DXY, rates, equities, risk regime."""

from __future__ import annotations

from typing import Any

from src.agents.base import AgentContext, BaseAgent


class MacroAgent(BaseAgent):
    name = "macro"
    model_key = "fast"

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "regime": "neutral",       # risk-on | neutral | risk-off
            "btc_bias": 0.0,           # -1..+1
            "leverage_cap": 1.0,       # 0..1 (fraction of full risk budget)
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
