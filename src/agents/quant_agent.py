"""Quant factor-ranking agent — momentum-tilted, regime-conditional, long-only.

Composite weights adapt to the market regime *hinted by KOSPI 3m momentum*:

    risk_on   M=0.45 V=0.15 Q=0.10 L=0.10 S=0.10 F=0.10   (let winners run)
    neutral   M=0.35 V=0.25 Q=0.15 L=0.10 S=0.05 F=0.10
    risk_off  M=0.10 V=0.20 Q=0.30 L=0.25 S=0.05 F=0.10   (defensive)

Weights are also exposed in the prompt so the LLM can reason about them.
``flow`` (foreign + institutional 5d net-buy) gets a baseline 10% across all
regimes — it's a Korea-specific short-horizon alpha that helps catch
"가는 말" (early stages of bull stampedes).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from common.types import AgentProposal, Side, TickerView

from .base import BaseAgent

REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "risk_on": {
        "momentum": 0.35,
        "breakout": 0.15,
        "value": 0.10,
        "quality": 0.10,
        "lowvol": 0.05,
        "size": 0.10,
        "flow": 0.15,
    },
    "neutral": {
        "momentum": 0.30,
        "breakout": 0.05,
        "value": 0.20,
        "quality": 0.15,
        "lowvol": 0.10,
        "size": 0.05,
        "flow": 0.15,
    },
    "risk_off": {
        "momentum": 0.10,
        "breakout": 0.00,
        "value": 0.20,
        "quality": 0.30,
        "lowvol": 0.25,
        "size": 0.05,
        "flow": 0.10,
    },
}


class QuantAgent(BaseAgent):
    name = "quant"
    prompt_file = "quant.md"
    use_rag = False  # numeric agent; RAG noise is unhelpful here

    def gather_context(self, as_of: date) -> dict[str, Any]:
        from data.market import fetch_factor_panel

        panel = fetch_factor_panel(as_of)
        rows = panel.get("rows", [])
        regime = panel.get("regime_hint", "neutral")
        weights = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["neutral"])

        for r in rows:
            r["composite"] = round(
                sum(weights[f] * r.get(f, 0.0) for f in weights),
                4,
            )

        # In risk_on, prioritise positive-momentum names (let winners run).
        # In risk_off, drop negative-composite names entirely.
        if regime == "risk_on":
            rows = [r for r in rows if r.get("momentum", 0.0) > -0.5]
        elif regime == "risk_off":
            rows = [r for r in rows if r["composite"] > 0]

        rows.sort(key=lambda r: r["composite"], reverse=True)
        panel["rows"] = rows[:25]
        panel["composite_weights"] = weights
        panel["regime_used"] = regime
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
