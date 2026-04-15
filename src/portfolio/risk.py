"""Risk metrics — MDD, Sharpe, Sortino, VaR."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class RiskMetrics:
    cagr: float
    volatility: float
    sharpe: float
    sortino: float
    mdd: float
    calmar: float
    var_95: float
    hit_ratio: float


def compute_portfolio_metrics(
    returns: pd.Series, rf: float = 0.03
) -> RiskMetrics:
    """Compute core risk/return metrics from a daily return series."""
    if returns.empty:
        return RiskMetrics(0, 0, 0, 0, 0, 0, 0, 0)

    equity_curve = (1 + returns).cumprod()
    years = len(returns) / 252 if len(returns) else 1
    cagr = equity_curve.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
    vol = returns.std() * np.sqrt(252)
    excess = returns.mean() * 252 - rf
    sharpe = excess / vol if vol > 0 else 0.0

    downside = returns[returns < 0]
    downside_std = downside.std() * np.sqrt(252) if not downside.empty else 0
    sortino = excess / downside_std if downside_std > 0 else 0.0

    rolling_max = equity_curve.cummax()
    drawdown = (equity_curve - rolling_max) / rolling_max
    mdd = float(drawdown.min())
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0

    var_95 = float(np.percentile(returns, 5))
    hit_ratio = float((returns > 0).mean())

    return RiskMetrics(
        cagr=float(cagr),
        volatility=float(vol),
        sharpe=float(sharpe),
        sortino=float(sortino),
        mdd=mdd,
        calmar=float(calmar),
        var_95=var_95,
        hit_ratio=hit_ratio,
    )
