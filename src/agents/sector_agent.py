"""Sector rotation agent."""
from __future__ import annotations

from datetime import date
from typing import Any

from common.types import AgentProposal

from .base import BaseAgent


class SectorAgent(BaseAgent):
    name = "sector"
    prompt_file = "sector.md"

    def gather_context(self, as_of: date) -> dict[str, Any]:
        from data.market import fetch_sector_snapshot
        from data.news import fetch_news_headlines

        ctx = fetch_sector_snapshot(as_of)
        news = fetch_news_headlines(as_of, limit=30)
        ctx["news"] = [
            {"headline": h["headline"], "tickers": h.get("tickers", [])}
            for h in news.get("headlines", [])[:30]
        ]
        return ctx

    def parse_response(self, text: str, as_of: date) -> AgentProposal:
        data = self._extract_json(text)
        tilts: dict[str, float] = {}
        for entry in data.get("sector_tilts", []):
            tilts[entry["sector"]] = float(entry["tilt"])
        return AgentProposal(
            agent_name=self.name,
            as_of=as_of,
            conviction=int(data.get("conviction", 5)),
            rationale=data.get("rationale", ""),
            sector_tilts=tilts,
        )
