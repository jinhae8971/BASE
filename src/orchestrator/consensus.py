"""Combine specialist AgentProposals into a single set of views."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date

from common.config import get_setting
from common.logging import get_logger
from common.types import AgentProposal, MarketRegime

log = get_logger(__name__)


class Consensus:
    """Conviction-weighted voting across specialist proposals.

    Produces:
    - `equity_weight`  (from macro)
    - `sector_tilts`   (from sector, weighted)
    - `ticker_scores`  (merged from value + quant, weighted by conviction)
    """

    def __init__(self) -> None:
        self.weights: dict[str, float] = get_setting("consensus.weights", {}) or {}
        self.min_conviction: int = int(get_setting("consensus.min_conviction", 5))
        self.min_agreement: int = int(get_setting("consensus.min_agreement", 2))

    def aggregate(
        self, proposals: Iterable[AgentProposal], as_of: date
    ) -> dict:
        proposals = [p for p in proposals if p.conviction >= self.min_conviction]
        if not proposals:
            log.warning("consensus.no_proposals_met_min_conviction")
            return self._default_view(as_of)

        equity_weight = self._vote_equity_weight(proposals)
        regime = self._vote_regime(proposals)
        sector_tilts = self._vote_sector_tilts(proposals)
        ticker_scores = self._vote_ticker_scores(proposals)

        return {
            "as_of": as_of,
            "equity_weight": equity_weight,
            "regime": regime,
            "sector_tilts": sector_tilts,
            "ticker_scores": ticker_scores,
        }

    # ------------------------------------------------------------------
    def _weight(self, agent: str) -> float:
        return float(self.weights.get(agent, 0.2))

    def _vote_equity_weight(self, proposals: list[AgentProposal]) -> float:
        vals, wts = [], []
        for p in proposals:
            if p.equity_weight is None:
                continue
            w = self._weight(p.agent_name) * (p.conviction / 10.0)
            vals.append(p.equity_weight)
            wts.append(w)
        if not vals:
            return 0.7
        total = sum(wts) or 1.0
        return sum(v * w for v, w in zip(vals, wts, strict=False)) / total

    def _vote_regime(self, proposals: list[AgentProposal]) -> MarketRegime:
        tally: dict[MarketRegime, float] = defaultdict(float)
        for p in proposals:
            if p.regime is None:
                continue
            tally[p.regime] += self._weight(p.agent_name) * (p.conviction / 10.0)
        if not tally:
            return MarketRegime.NEUTRAL
        return max(tally.items(), key=lambda kv: kv[1])[0]

    def _vote_sector_tilts(self, proposals: list[AgentProposal]) -> dict[str, float]:
        tilts: dict[str, float] = defaultdict(float)
        weight_sum: dict[str, float] = defaultdict(float)
        for p in proposals:
            if not p.sector_tilts:
                continue
            w = self._weight(p.agent_name) * (p.conviction / 10.0)
            for sector, t in p.sector_tilts.items():
                tilts[sector] += t * w
                weight_sum[sector] += w
        return {s: tilts[s] / weight_sum[s] for s in tilts if weight_sum[s] > 0}

    def _vote_ticker_scores(
        self, proposals: list[AgentProposal]
    ) -> dict[str, dict]:
        scores: dict[str, float] = defaultdict(float)
        mentions: dict[str, int] = defaultdict(int)
        rationales: dict[str, list[str]] = defaultdict(list)

        for p in proposals:
            for pick in p.picks:
                w = self._weight(p.agent_name) * (p.conviction / 10.0)
                contribution = (pick.score or 1.0) * w
                scores[pick.ticker] += contribution
                mentions[pick.ticker] += 1
                if pick.rationale:
                    rationales[pick.ticker].append(f"[{p.agent_name}] {pick.rationale}")

        return {
            t: {
                "score": scores[t],
                "mentions": mentions[t],
                "rationale": " | ".join(rationales[t]),
            }
            for t in scores
            if mentions[t] >= self.min_agreement or scores[t] >= 1.0
        }

    def _default_view(self, as_of: date) -> dict:
        return {
            "as_of": as_of,
            "equity_weight": 0.5,
            "regime": MarketRegime.NEUTRAL,
            "sector_tilts": {},
            "ticker_scores": {},
        }
