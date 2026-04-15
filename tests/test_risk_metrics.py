from __future__ import annotations

import numpy as np
import pandas as pd

from portfolio.risk import compute_portfolio_metrics


def test_flat_returns_give_zero_metrics() -> None:
    returns = pd.Series(np.zeros(252))
    m = compute_portfolio_metrics(returns)
    assert m.cagr == 0
    assert m.volatility == 0
    assert m.mdd == 0


def test_drawdown_computation() -> None:
    # 10% gain, then 20% drop
    returns = pd.Series([0.10] + [0.0] * 10 + [-0.20] + [0.0] * 10)
    m = compute_portfolio_metrics(returns)
    assert m.mdd < 0
    assert m.hit_ratio > 0
