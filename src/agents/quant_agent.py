"""Quant factor-ranking agent."""
from __future__ import annotations

from datetime import date
from typing import Any

from common.types import AgentProposal, Side, TickerView

from .base import BaseAgent


class QuantAgent(BaseAgent):
    name = "quant"
    prompt_file = "quant.md"

    def gather_context(self, as_of: date) -> dict[str, Any]:
        from data.market import fetch_factor_panel

        return fetch_factor_panel(as_of)

    def parse_response(self, text: str, as_of: date) -> AgentProposal:
        data = self._extract_json(text)
        picks: list[TickerView] = []
        for p in data.get("picks", []):
            picks.append(
                TickerView(
                    ticker=str(p["ticker"]),
                    name=p.get("name"),
                    side=Side(p.get("side", "BUY").upper()),
                    target_weight=p.get("target_weight"),
                    score=p.get("score"),
                    rationale=p.get("rationale"),
                )
            )
        return AgentProposal(
            agent_name=self.name,
            as_of=as_of,
            conviction=int(data.get("conviction", 5)),
            rationale=data.get("rationale", ""),
            picks=picks,
        )
