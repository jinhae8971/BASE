"""Performance metrics.

The system's two primary KPIs are **return** and **MDD**, and the single
most important number is **Alpha vs BTC** (rolling 3-month for production,
total-period for the initial backtest). Everything else in this module is
supporting detail.

All inputs are plain Python lists — no pandas dependency — so the module is
fast, dep-free, and importable from tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence


@dataclass
class PerformanceReport:
    start_equity: float
    end_equity: float
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    sortino: float
    mdd_pct: float
    win_rate_pct: float
    profit_factor: float
    turnover_pct: float
    num_days: int

    # Benchmark (BTC HODL) comparison
    btc_total_return_pct: float
    alpha_vs_btc_pp: float         # percentage points
    rolling_alpha_3m_pp: float     # mean of 90-day rolling (total - btc) returns

    def summary(self) -> str:
        return (
            f"Return {self.total_return_pct:+.2f}% | "
            f"CAGR {self.cagr_pct:+.2f}% | "
            f"Sharpe {self.sharpe:.2f} | "
            f"Sortino {self.sortino:.2f} | "
            f"MDD {self.mdd_pct:.2f}% | "
            f"WinRate {self.win_rate_pct:.1f}% | "
            f"BTC {self.btc_total_return_pct:+.2f}% | "
            f"Alpha {self.alpha_vs_btc_pp:+.2f}pp"
        )


def daily_returns(equity: Sequence[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(equity)):
        prev = equity[i - 1]
        out.append((equity[i] / prev - 1.0) if prev > 0 else 0.0)
    return out


def max_drawdown(equity: Sequence[float]) -> float:
    peak = -math.inf
    mdd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        if peak > 0:
            dd = 1.0 - e / peak
            if dd > mdd:
                mdd = dd
    return mdd


def _sharpe(rets: Sequence[float], periods_per_year: int = 365) -> float:
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    stdev = math.sqrt(max(var, 0.0))
    if stdev == 0:
        return 0.0
    return (mean / stdev) * math.sqrt(periods_per_year)


def _sortino(rets: Sequence[float], periods_per_year: int = 365) -> float:
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    downside = [min(0.0, r) for r in rets]
    var = sum(d * d for d in downside) / len(rets)
    stdev = math.sqrt(var)
    if stdev == 0:
        return 0.0
    return (mean / stdev) * math.sqrt(periods_per_year)


def _win_rate(rets: Sequence[float]) -> float:
    if not rets:
        return 0.0
    wins = sum(1 for r in rets if r > 0)
    return 100.0 * wins / len(rets)


def _profit_factor(rets: Sequence[float]) -> float:
    gains = sum(r for r in rets if r > 0)
    losses = -sum(r for r in rets if r < 0)
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def _rolling_alpha(
    strat_rets: Sequence[float],
    btc_rets: Sequence[float],
    window: int = 90,
) -> float:
    if len(strat_rets) < window or len(btc_rets) < window:
        return 0.0
    diffs: list[float] = []
    for start in range(0, len(strat_rets) - window + 1):
        s = _compound(strat_rets[start : start + window])
        b = _compound(btc_rets[start : start + window])
        diffs.append((s - b) * 100.0)
    return sum(diffs) / len(diffs) if diffs else 0.0


def _compound(rets: Sequence[float]) -> float:
    out = 1.0
    for r in rets:
        out *= 1.0 + r
    return out - 1.0


def compute_metrics(
    days: Sequence[datetime],
    equity: Sequence[float],
    btc_equity: Sequence[float],
    gross_traded_usd: float = 0.0,
) -> PerformanceReport:
    assert len(days) == len(equity) == len(btc_equity), "length mismatch"
    rets = daily_returns(equity)
    btc_rets = daily_returns(btc_equity)

    start = equity[0]
    end = equity[-1]
    total = (end / start - 1.0) if start > 0 else 0.0
    n_days = max(1, len(days) - 1)
    cagr = (end / start) ** (365.0 / n_days) - 1.0 if start > 0 else 0.0

    btc_total = (btc_equity[-1] / btc_equity[0] - 1.0) if btc_equity[0] > 0 else 0.0
    alpha = (total - btc_total) * 100.0

    avg_equity = sum(equity) / max(1, len(equity))
    turnover_pct = 100.0 * gross_traded_usd / avg_equity if avg_equity > 0 else 0.0

    return PerformanceReport(
        start_equity=start,
        end_equity=end,
        total_return_pct=total * 100.0,
        cagr_pct=cagr * 100.0,
        sharpe=_sharpe(rets),
        sortino=_sortino(rets),
        mdd_pct=max_drawdown(equity) * 100.0,
        win_rate_pct=_win_rate(rets),
        profit_factor=_profit_factor(rets),
        turnover_pct=turnover_pct,
        num_days=len(days),
        btc_total_return_pct=btc_total * 100.0,
        alpha_vs_btc_pp=alpha,
        rolling_alpha_3m_pp=_rolling_alpha(rets, btc_rets, window=90),
    )
