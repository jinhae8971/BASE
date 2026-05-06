"""Portfolio optimizer — converts consensus view into PortfolioTarget.

Two methods are wired:

    score_weighted   (default fallback) — score-proportional allocation with
                     position / sector / cash caps applied iteratively.

    mean_variance    PyPortfolioOpt's max-Sharpe / Min-Vol on the consensus
                     candidates' historical returns.

    black_litterman  Combine the consensus equity_weight + score signal with
                     the empirical covariance via the BL model.

If the optimizer dependencies (cvxpy / PyPortfolioOpt) are unavailable, we
silently fall back to score-weighted.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from common.config import get_setting
from common.logging import get_logger
from common.types import PortfolioTarget

log = get_logger(__name__)


class PortfolioOptimizer:
    def __init__(self) -> None:
        self.max_pos: float = float(get_setting("risk.max_position_weight", 0.10))
        self.max_sector: float = float(get_setting("risk.max_sector_weight", 0.30))
        self.cash_buffer_min: float = float(get_setting("risk.cash_buffer_min", 0.05))
        self.method: str = str(get_setting("optimizer.method", "score_weighted"))
        self.lookback_days: int = int(get_setting("optimizer.lookback_days", 252))

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

        method = self.method
        weights: dict[str, float] | None = None
        if method in ("mean_variance", "black_litterman"):
            try:
                weights = self._optimize_quant(
                    list(ticker_scores.keys()), ticker_scores, equity_weight, as_of, method
                )
            except Exception as e:
                log.warning("optimizer.quant_failed", method=method, error=str(e))
                weights = None
        if weights is None:
            weights = self._score_weighted(ticker_scores, equity_weight)

        weights = {t: min(w, self.max_pos) for t, w in weights.items()}
        weights = self._enforce_sector_cap(weights, sector_map or {})
        # Renormalize to target equity_weight
        total_w = sum(weights.values())
        if total_w > equity_weight and total_w > 0:
            scale = equity_weight / total_w
            weights = {t: w * scale for t, w in weights.items()}
        cash = max(1.0 - sum(weights.values()), 0.0)

        return PortfolioTarget(
            as_of=as_of,
            cash_weight=cash,
            positions=weights,
            rationale=f"method={method} equity={equity_weight:.2f}",
        )

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------
    def _score_weighted(
        self, ticker_scores: dict[str, dict], equity_weight: float
    ) -> dict[str, float]:
        total = sum(max(v["score"], 0.0) for v in ticker_scores.values()) or 1.0
        return {
            t: equity_weight * max(v["score"], 0.0) / total
            for t, v in ticker_scores.items()
        }

    def _optimize_quant(
        self,
        tickers: list[str],
        ticker_scores: dict[str, dict],
        equity_weight: float,
        as_of: date,
        method: str,
    ) -> dict[str, float]:
        from data.market import fetch_close_panel  # local import → stays optional

        start = as_of - timedelta(days=self.lookback_days * 2)
        panel = fetch_close_panel(tickers, start, as_of)
        if panel.empty or panel.shape[1] < 2 or len(panel) < 60:
            raise RuntimeError("insufficient price history for quant optimizer")
        rets = panel.pct_change().dropna()

        if method == "mean_variance":
            return self._mean_variance(panel, rets, equity_weight)
        return self._black_litterman(panel, rets, ticker_scores, equity_weight)

    def _mean_variance(
        self, panel: pd.DataFrame, rets: pd.DataFrame, equity_weight: float
    ) -> dict[str, float]:
        from pypfopt import EfficientFrontier, expected_returns, risk_models

        mu = expected_returns.mean_historical_return(panel, frequency=252)
        cov = risk_models.CovarianceShrinkage(panel).ledoit_wolf()
        ef = EfficientFrontier(mu, cov, weight_bounds=(0, self.max_pos))
        ef.max_sharpe(risk_free_rate=0.03)
        raw = ef.clean_weights()
        return {t: float(w) * equity_weight for t, w in raw.items() if w > 1e-4}

    def _black_litterman(
        self,
        panel: pd.DataFrame,
        rets: pd.DataFrame,
        ticker_scores: dict[str, dict],
        equity_weight: float,
    ) -> dict[str, float]:
        from pypfopt import BlackLittermanModel, EfficientFrontier, risk_models

        cov = risk_models.CovarianceShrinkage(panel).ledoit_wolf()
        # Treat each consensus score as a *view* on absolute return:
        #   higher score → higher expected return tilt.
        max_score = max((v["score"] for v in ticker_scores.values()), default=1.0) or 1.0
        viewdict = {t: 0.05 * (v["score"] / max_score) for t, v in ticker_scores.items()}

        # Equilibrium prior: equal-weight market.
        n = panel.shape[1]
        market_prior = pd.Series(np.ones(n) / n, index=panel.columns)

        bl = BlackLittermanModel(
            cov,
            pi=market_prior,
            absolute_views=viewdict,
        )
        ret_bl = bl.bl_returns()
        ef = EfficientFrontier(ret_bl, cov, weight_bounds=(0, self.max_pos))
        ef.max_sharpe(risk_free_rate=0.03)
        raw = ef.clean_weights()
        return {t: float(w) * equity_weight for t, w in raw.items() if w > 1e-4}

    # ------------------------------------------------------------------
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
