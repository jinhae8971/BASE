from __future__ import annotations

import numpy as np
import pandas as pd

from portfolio.risk import compute_portfolio_metrics


def test_alpha_beta_ir_computed_with_benchmark() -> None:
    rng = np.random.default_rng(7)
    n = 1000
    bench = pd.Series(rng.normal(0.0004, 0.011, n))
    # Portfolio = 1.2 * bench + meaningful alpha + small noise
    port = 0.0008 + 1.2 * bench + pd.Series(rng.normal(0.0, 0.003, n))

    m = compute_portfolio_metrics(port, benchmark_returns=bench)
    assert 0.5 < m.beta < 1.8
    assert m.tracking_error > 0
    # Active return is positive on average → IR should be > 0
    active = (port - bench).mean()
    assert active > 0
    assert m.information_ratio > 0


def test_no_benchmark_skips_relative_metrics() -> None:
    returns = pd.Series([0.001] * 252)
    m = compute_portfolio_metrics(returns)
    assert m.alpha == 0
    assert m.beta == 0
    assert m.information_ratio == 0
