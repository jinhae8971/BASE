"""Value Investor agent — tokenomics, fair value, conviction horizon."""

from __future__ import annotations

from typing import Any

from src.agents.base import AgentContext, BaseAgent


class ValueAgent(BaseAgent):
    name = "value"
    model_key = "strong"  # Opus — high-stakes fundamental call

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "coins": {
                sym: {
                    "fair_value_ratio": 1.0,  # price / estimated fair value
                    "conviction": 0.0,        # -1..+1
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
