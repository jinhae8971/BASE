"""Crypto Quant agent — price/volatility signals."""

from __future__ import annotations

from typing import Any

from src.agents.base import AgentContext, BaseAgent


class QuantAgent(BaseAgent):
    name = "quant"
    model_key = "fast"

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "coins": {
                sym: {
                    "signal": 0.0,       # -1..+1
                    "vol_target": 0.02,  # daily stdev target for sizing
                    "stop_pct": 8.0,
                }
                for sym in ctx.universe
            },
            "vol_regime": "normal",
        }

    def _validate(self, payload: dict[str, Any]) -> None:
        if "coins" not in payload:
            raise ValueError("quant: missing coins")
        for sym, row in payload["coins"].items():
            if not -1.0 <= float(row["signal"]) <= 1.0:
                raise ValueError(f"quant: {sym} signal out of range")
            if float(row["vol_target"]) <= 0:
                raise ValueError(f"quant: {sym} vol_target must be positive")
