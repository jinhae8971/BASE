"""Macro regime agent — decides overall equity vs cash weight."""
from __future__ import annotations

from datetime import date
from typing import Any

from common.types import AgentProposal, MarketRegime

from .base import BaseAgent


class MacroAgent(BaseAgent):
    name = "macro"
    prompt_file = "macro.md"

    def gather_context(self, as_of: date) -> dict[str, Any]:
        from data.macro import fetch_macro_snapshot
        from data.news import fetch_news_headlines
        from data.news_global import fetch_global_macro_headlines

        snap = fetch_macro_snapshot(as_of)
        kr_news = fetch_news_headlines(as_of, limit=20)
        global_news = fetch_global_macro_headlines(as_of, limit=20)
        snap["recent_news_kr"] = kr_news.get("headlines", [])[:20]
        snap["recent_news_global"] = global_news.get("headlines", [])[:20]
        return snap

    def parse_response(self, text: str, as_of: date) -> AgentProposal:
        data = self._extract_json(text)
        regime_str = data.get("regime", "neutral")
        try:
            regime = MarketRegime(regime_str)
        except ValueError:
            regime = MarketRegime.NEUTRAL
        return AgentProposal(
            agent_name=self.name,
            as_of=as_of,
            conviction=int(data.get("conviction", 5)),
            rationale=data.get("rationale", ""),
            equity_weight=float(data.get("equity_weight", 0.7)),
            regime=regime,
        )
