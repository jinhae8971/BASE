"""Portfolio optimizer — converts consensus view into PortfolioTarget."""
from __future__ import annotations

from datetime import date
from typing import Any

from common.config import get_setting
from common.logging import get_logger
from common.types import PortfolioTarget

log = get_logger(__name__)


class PortfolioOptimizer:
    """Lightweight optimizer.

    For Phase 2 we use a simple score-weighted allocator with risk-limit
    clipping. Phase 3 will swap in cvxpy/PyPortfolioOpt (Mean-Variance or
    Black-Litterman) against the actual covariance matrix.
    """

    def __init__(self) -> None:
        self.max_pos: float = float(get_setting("risk.max_position_weight", 0.10))
        self.max_sector: float = float(get_setting("risk.max_sector_weight", 0.30))
        self.cash_buffer_min: float = float(get_setting("risk.cash_buffer_min", 0.05))
        self.method: str = str(get_setting("optimizer.method", "mean_variance"))

    def optimize(
        self,
        consensus: dict[str, Any],
        *,
        sector_map: dict[str, str] | None = None,
    ) -> PortfolioTarget:
        as_of: date = consensus["as_of"]
        equity_weight: float = max(
            0.0, min(1.0 - self.cash_buffer_min, consensus["equity_weight"])
        )
        ticker_scores: dict[str, dict] = consensus.get("ticker_scores", {})

        if not ticker_scores:
            log.warning("optimizer.no_tickers")
            return PortfolioTarget(
                as_of=as_of,
                cash_weight=1.0,
                positions={},
                rationale="No consensus picks available.",
            )

        total = sum(max(v["score"], 0.0) for v in ticker_scores.values()) or 1.0
        raw: dict[str, float] = {
            t: equity_weight * max(v["score"], 0.0) / total
            for t, v in ticker_scores.items()
        }

        # Per-name cap
        clipped = {t: min(w, self.max_pos) for t, w in raw.items()}

        # Sector cap
        clipped = self._enforce_sector_cap(clipped, sector_map or {})

        # Renormalize to target equity_weight
        total_w = sum(clipped.values())
        if total_w > equity_weight and total_w > 0:
            scale = equity_weight / total_w
            clipped = {t: w * scale for t, w in clipped.items()}

        cash = 1.0 - sum(clipped.values())
        return PortfolioTarget(
            as_of=as_of,
            cash_weight=max(cash, 0.0),
            positions=clipped,
            rationale=f"method={self.method} equity={equity_weight:.2f}",
        )

    def _enforce_sector_cap(
        self, weights: dict[str, float], sector_map: dict[str, str]
    ) -> dict[str, float]:
        if not sector_map:
            return weights
        sector_totals: dict[str, float] = {}
        for t, w in weights.items():
            sec = sector_map.get(t, "UNKNOWN")
            sector_totals[sec] = sector_totals.get(sec, 0.0) + w
        adjusted = dict(weights)
        for sec, total in sector_totals.items():
            if total <= self.max_sector:
                continue
            scale = self.max_sector / total
            for t in [t for t, _ in weights.items() if sector_map.get(t) == sec]:
                adjusted[t] *= scale
            log.warning("optimizer.sector_cap", sector=sec, total=total, scale=scale)
        return adjusted
