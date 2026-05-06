"""Risk & performance metrics — standalone & benchmark-relative."""
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
    # Benchmark-relative (filled when benchmark series is supplied)
    alpha: float = 0.0
    beta: float = 0.0
    tracking_error: float = 0.0
    information_ratio: float = 0.0


def compute_portfolio_metrics(
    returns: pd.Series,
    *,
    rf: float = 0.03,
    benchmark_returns: pd.Series | None = None,
) -> RiskMetrics:
    """Compute core risk/return metrics from a daily return series.

    Set ``benchmark_returns`` to also fill alpha / beta / tracking-error /
    information-ratio.
    """
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

    alpha = beta = te = ir = 0.0
    if benchmark_returns is not None and not benchmark_returns.empty:
        alpha, beta, te, ir = _benchmark_relative(returns, benchmark_returns, rf=rf)

    return RiskMetrics(
        cagr=float(cagr),
        volatility=float(vol),
        sharpe=float(sharpe),
        sortino=float(sortino),
        mdd=mdd,
        calmar=float(calmar),
        var_95=var_95,
        hit_ratio=hit_ratio,
        alpha=float(alpha),
        beta=float(beta),
        tracking_error=float(te),
        information_ratio=float(ir),
    )


def _benchmark_relative(
    portfolio: pd.Series, benchmark: pd.Series, *, rf: float
) -> tuple[float, float, float, float]:
    """Annualized alpha / beta / tracking-error / information-ratio."""
    aligned = pd.concat([portfolio, benchmark], axis=1, join="inner").dropna()
    if aligned.empty or len(aligned) < 30:
        return 0.0, 0.0, 0.0, 0.0
    aligned.columns = ["p", "b"]

    excess_p = aligned["p"] - rf / 252
    excess_b = aligned["b"] - rf / 252

    cov = float(np.cov(excess_p, excess_b, ddof=1)[0, 1])
    var_b = float(excess_b.var(ddof=1))
    beta = cov / var_b if var_b > 0 else 0.0

    # Annualised CAPM alpha
    ann_p = (1 + aligned["p"]).prod() ** (252 / len(aligned)) - 1
    ann_b = (1 + aligned["b"]).prod() ** (252 / len(aligned)) - 1
    alpha = ann_p - (rf + beta * (ann_b - rf))

    active = aligned["p"] - aligned["b"]
    te = float(active.std() * np.sqrt(252))
    ir = float(active.mean() * 252 / te) if te > 0 else 0.0

    return alpha, beta, te, ir
