"""Event-driven backtest engine.

The engine replays a quant-only allocation pipeline over historical bars:

    1. For each rebalance date, fetch the factor panel using only data up to
       that date (no look-ahead — implemented by the caller via ``as_of``).
    2. Build a target portfolio (score-weighted by composite factor) under
       the same risk caps as the live ``PortfolioOptimizer``.
    3. Simulate the rebalance: pay slippage + commission + tax.
    4. Mark-to-market the portfolio every trading day.
    5. Compute returns, equity curve, and per-rebalance trade log.

This is intentionally a *deterministic* backtest — it does **not** call the
LLMs (that would be too slow and too expensive). The LLM agents are validated
in paper trading (Phase 5), not in backtest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from common.config import get_setting
from common.logging import get_logger
from data.market import fetch_benchmark_series, fetch_close_panel, fetch_factor_panel
from data.universe import get_universe, sector_map
from portfolio.risk import RiskMetrics, compute_portfolio_metrics

log = get_logger(__name__)


@dataclass
class BacktestResult:
    start: date
    end: date
    equity_curve: pd.Series
    returns: pd.Series
    metrics: RiskMetrics
    benchmark_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    benchmark_metrics: RiskMetrics | None = None
    trade_log: list[dict[str, Any]] = field(default_factory=list)
    weights_log: list[dict[str, Any]] = field(default_factory=list)


class BacktestEngine:
    def __init__(
        self,
        start: date | None = None,
        end: date | None = None,
        initial_capital: float | None = None,
        rebalance: str = "monthly",
        top_n: int = 15,
    ) -> None:
        self.start = start or date.fromisoformat(
            str(get_setting("backtest.start", "2015-01-01"))
        )
        self.end = end or date.today()
        self.initial_capital = float(
            initial_capital or get_setting("backtest.initial_capital", 100_000_000)
        )
        self.rebalance = rebalance
        self.top_n = top_n

        self.max_pos = float(get_setting("risk.max_position_weight", 0.10))
        self.max_sector = float(get_setting("risk.max_sector_weight", 0.30))
        self.cash_buffer_min = float(get_setting("risk.cash_buffer_min", 0.05))
        self.slippage_bps = float(get_setting("execution.slippage_bps", 15))
        self.commission_bps = float(get_setting("execution.commission_bps", 1.5))
        self.tax_sell_bps = float(get_setting("execution.tax_sell_bps", 23))

    # ------------------------------------------------------------------
    def run(self) -> BacktestResult:
        log.info(
            "backtest.start",
            start=self.start.isoformat(),
            end=self.end.isoformat(),
            method="quant_score_weighted",
            rebalance=self.rebalance,
        )
        universe = get_universe(self.end)
        tickers = [r["ticker"] for r in universe]
        smap = sector_map(self.end)

        prices = fetch_close_panel(tickers, self.start - timedelta(days=400), self.end)
        if prices.empty:
            log.warning("backtest.no_prices")
            return self._empty_result()

        prices = prices.dropna(how="all")
        rebalance_dates = self._rebalance_dates(prices.index)
        weights = pd.Series(0.0, index=prices.columns)
        cash = 1.0  # in NAV units
        nav_history: list[tuple[pd.Timestamp, float]] = []
        nav = 1.0
        prev_nav = 1.0
        last_close: pd.Series | None = None
        trade_log: list[dict[str, Any]] = []
        weights_log: list[dict[str, Any]] = []

        for ts, row in prices.iterrows():
            close = row.fillna(method="ffill") if hasattr(row, "fillna") else row
            if last_close is not None:
                # Mark to market: holdings drift with returns.
                ret = (close / last_close).replace([np.inf, -np.inf], 1.0).fillna(1.0)
                weights = weights * ret
                nav = cash + float(weights.sum())
                # Renormalize weights against the new NAV
                if nav > 0:
                    weights = weights / nav
                    cash = cash / nav
                    nav = 1.0
            last_close = close

            if ts.date() in rebalance_dates:
                target_w = self._build_target(ts.date(), prices.loc[:ts], smap)
                # Compute trade cost on absolute weight change
                delta = (target_w.reindex(weights.index).fillna(0) - weights).abs()
                buy_cost = (delta * (self.slippage_bps + self.commission_bps) / 10_000).sum()
                sell_cost = (
                    delta
                    * (self.slippage_bps + self.commission_bps + self.tax_sell_bps)
                    / 10_000
                ).sum()
                cost = float((buy_cost + sell_cost) / 2)
                weights = target_w.reindex(weights.index).fillna(0)
                cash = max(1.0 - float(weights.sum()) - cost, 0.0)
                trade_log.append(
                    {"date": ts.date().isoformat(), "cost": cost, "n_pos": int((weights > 0).sum())}
                )
                weights_log.append(
                    {"date": ts.date().isoformat(), **{t: float(w) for t, w in weights.items() if w > 0}}
                )

            nav_today = cash + float(weights.sum())
            nav_history.append((ts, nav_today * (prev_nav if not nav_history else self._init_nav(nav_history))))

        # Build equity curve from NAV ratios
        idx = [t for t, _ in nav_history]
        nav_values = pd.Series([v for _, v in nav_history], index=idx)
        # Recompute as cumulative product of period ratios (more numerically stable)
        equity = self._normalize_to_initial(nav_values)
        returns = equity.pct_change().fillna(0.0)

        bench = fetch_benchmark_series(self.start, self.end)
        if not bench.empty:
            bench = bench.reindex(equity.index, method="pad").dropna()
            bench_norm = bench / bench.iloc[0] * self.initial_capital
            bench_returns = bench.pct_change().fillna(0.0)
            metrics = compute_portfolio_metrics(returns, benchmark_returns=bench_returns)
            bench_metrics = compute_portfolio_metrics(bench_returns)
        else:
            bench_norm = pd.Series(dtype=float)
            metrics = compute_portfolio_metrics(returns)
            bench_metrics = None

        log.info(
            "backtest.done",
            cagr=round(metrics.cagr, 4),
            mdd=round(metrics.mdd, 4),
            sharpe=round(metrics.sharpe, 2),
            ir=round(metrics.information_ratio, 2),
            te=round(metrics.tracking_error, 4),
        )
        return BacktestResult(
            start=self.start,
            end=self.end,
            equity_curve=equity,
            returns=returns,
            metrics=metrics,
            benchmark_curve=bench_norm,
            benchmark_metrics=bench_metrics,
            trade_log=trade_log,
            weights_log=weights_log,
        )

    # ------------------------------------------------------------------
    def _empty_result(self) -> BacktestResult:
        idx = pd.date_range(self.start, self.end, freq="B")
        eq = pd.Series(self.initial_capital, index=idx)
        ret = pd.Series(0.0, index=idx)
        return BacktestResult(
            start=self.start,
            end=self.end,
            equity_curve=eq,
            returns=ret,
            metrics=compute_portfolio_metrics(ret),
        )

    def _init_nav(self, history: list[tuple[pd.Timestamp, float]]) -> float:
        if not history:
            return 1.0
        return history[-1][1]

    def _normalize_to_initial(self, nav: pd.Series) -> pd.Series:
        if nav.empty:
            return nav
        # Ratio-based reconstruction so floating-point drift doesn't snowball.
        ratios = nav / nav.shift(1)
        ratios = ratios.fillna(1.0)
        equity = (ratios.cumprod()) * self.initial_capital
        return equity

    def _rebalance_dates(self, idx: pd.DatetimeIndex) -> set[date]:
        """Pick the first trading day of each month/week."""
        if self.rebalance == "weekly":
            keys = idx.to_series().groupby([idx.year, idx.isocalendar().week]).first()
        else:
            keys = idx.to_series().groupby([idx.year, idx.month]).first()
        return {pd.Timestamp(d).date() for d in keys.values}

    def _build_target(
        self,
        as_of: date,
        prices_to_date: pd.DataFrame,
        smap: dict[str, str],
    ) -> pd.Series:
        panel = fetch_factor_panel(as_of)
        rows = panel.get("rows", [])
        if not rows:
            return pd.Series(0.0, index=prices_to_date.columns)

        for r in rows:
            r["composite"] = (
                0.30 * r.get("value", 0)
                + 0.25 * r.get("momentum", 0)
                + 0.25 * r.get("quality", 0)
                + 0.10 * r.get("lowvol", 0)
                + 0.10 * r.get("size", 0)
            )
        rows.sort(key=lambda r: r["composite"], reverse=True)
        top = [r for r in rows if r["composite"] > 0][: self.top_n]
        total = sum(r["composite"] for r in top) or 1.0
        equity_w = 1.0 - self.cash_buffer_min
        target = pd.Series(0.0, index=prices_to_date.columns)
        for r in top:
            t = r["ticker"]
            if t not in target.index:
                continue
            target[t] = min(equity_w * r["composite"] / total, self.max_pos)

        # Sector cap
        sec_totals: dict[str, float] = {}
        for t, w in target.items():
            sec_totals[smap.get(t, "기타")] = sec_totals.get(smap.get(t, "기타"), 0.0) + w
        for sec, total_w in sec_totals.items():
            if total_w <= self.max_sector:
                continue
            scale = self.max_sector / total_w
            for t in target.index:
                if smap.get(t, "기타") == sec:
                    target[t] *= scale
        return target


def main() -> None:
    import typer

    app = typer.Typer()

    @app.command()
    def run(
        start: str = "2015-01-01",
        end: str | None = None,
        rebalance: str = "monthly",
        top_n: int = 15,
    ) -> None:
        eng = BacktestEngine(
            start=date.fromisoformat(start),
            end=date.fromisoformat(end) if end else None,
            rebalance=rebalance,
            top_n=top_n,
        )
        result = eng.run()
        typer.echo(f"Backtest: {result.start} → {result.end}")
        typer.echo(f"Metrics: {result.metrics}")
        if result.benchmark_metrics is not None:
            typer.echo(f"Benchmark: {result.benchmark_metrics}")
            alpha = result.metrics.cagr - result.benchmark_metrics.cagr
            typer.echo(f"Alpha (CAGR): {alpha:+.2%}")

    app()


if __name__ == "__main__":
    main()
