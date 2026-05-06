"""Quant factor-ranking agent.

Most of the heavy lifting (factor z-scoring) happens upstream in
``data.market.fetch_factor_panel``. We pre-compute a composite score and
hand the LLM a *short* shortlist so the prompt stays cheap and deterministic.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from common.types import AgentProposal, Side, TickerView

from .base import BaseAgent


class QuantAgent(BaseAgent):
    name = "quant"
    prompt_file = "quant.md"
    use_rag = False  # numeric agent; RAG noise is unhelpful here

    def gather_context(self, as_of: date) -> dict[str, Any]:
        from data.market import fetch_factor_panel

        panel = fetch_factor_panel(as_of)
        rows = panel.get("rows", [])

        # Composite score per the prompt-defined weights.
        for r in rows:
            r["composite"] = round(
                0.30 * r.get("value", 0)
                + 0.25 * r.get("momentum", 0)
                + 0.25 * r.get("quality", 0)
                + 0.10 * r.get("lowvol", 0)
                + 0.10 * r.get("size", 0),
                4,
            )
        rows.sort(key=lambda r: r["composite"], reverse=True)
        panel["rows"] = rows[:25]  # shortlist for the prompt
        return panel

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
