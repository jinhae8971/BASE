"""Event-driven backtest engine — KOSPI200 equal-weight benchmark replay.

Phase 3 plan: re-play full LLM pipeline per date.
Phase 1 (current): data-driven equal-weight KOSPI200 backtest using pykrx.
Falls back to flat equity curve if pykrx data is unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from common.config import get_setting
from common.logging import get_logger
from portfolio.risk import RiskMetrics, compute_portfolio_metrics

log = get_logger(__name__)

KOSPI200_IDX = "1028"


@dataclass
class BacktestResult:
    start: date
    end: date
    equity_curve: pd.Series
    returns: pd.Series
    metrics: RiskMetrics
    benchmark_returns: pd.Series = field(default_factory=pd.Series)
    trade_log: list[dict[str, Any]] = field(default_factory=list)


class BacktestEngine:
    def __init__(
        self,
        start: date | None = None,
        end: date | None = None,
        initial_capital: float | None = None,
    ) -> None:
        self.start = start or date.fromisoformat(
            str(get_setting("backtest.start", "2015-01-01"))
        )
        self.end = end or date.today()
        self.initial_capital = float(
            initial_capital or get_setting("backtest.initial_capital", 100_000_000)
        )

    # ------------------------------------------------------------------
    def run(self) -> BacktestResult:
        log.info("backtest.start", start=self.start, end=self.end)

        bench_returns = self._fetch_index_returns()
        if bench_returns.empty:
            log.warning("backtest.no_data_fallback")
            return self._flat_result()

        equity = (1 + bench_returns).cumprod() * self.initial_capital
        metrics = compute_portfolio_metrics(bench_returns)

        log.info(
            "backtest.done",
            cagr=round(metrics.cagr, 4),
            mdd=round(metrics.max_drawdown, 4),
            sharpe=round(metrics.sharpe, 4),
        )
        return BacktestResult(
            start=self.start,
            end=self.end,
            equity_curve=equity,
            returns=bench_returns,
            metrics=metrics,
            benchmark_returns=bench_returns,
        )

    # ------------------------------------------------------------------
    # Equal-weight KOSPI200 strategy
    # ------------------------------------------------------------------

    def _fetch_index_returns(self) -> pd.Series:
        """Fetch KOSPI200 index daily returns from pykrx."""
        try:
            from pykrx import stock  # type: ignore

            s = self.start.strftime("%Y%m%d")
            e = self.end.strftime("%Y%m%d")
            df = stock.get_index_ohlcv_by_date(s, e, KOSPI200_IDX)
            if df.empty:
                return pd.Series(dtype=float)

            close_col = "종가" if "종가" in df.columns else df.columns[3]
            close = df[close_col].dropna()
            rets = close.pct_change().dropna()
            rets.index = pd.to_datetime(rets.index)
            return rets

        except Exception as exc:
            log.warning("backtest.pykrx_failed", error=str(exc))
            return pd.Series(dtype=float)

    def _flat_result(self) -> BacktestResult:
        idx = pd.date_range(self.start, self.end, freq="B")
        returns = pd.Series(0.0, index=idx)
        equity = pd.Series(self.initial_capital, index=idx)
        metrics = compute_portfolio_metrics(returns)
        return BacktestResult(
            start=self.start,
            end=self.end,
            equity_curve=equity,
            returns=returns,
            metrics=metrics,
        )

    # ------------------------------------------------------------------
    # Simple equal-weight rebalancing backtest (top-N by market cap)
    # ------------------------------------------------------------------

    def run_equal_weight(self, top_n: int = 30) -> BacktestResult:
        """Monthly-rebalanced equal-weight portfolio of KOSPI200 top-N stocks."""
        try:
            from pykrx import stock  # type: ignore

            # Collect monthly rebalance dates
            dates = pd.date_range(self.start, self.end, freq="BMS")  # business month start
            portfolio_rets: list[pd.Series] = []

            for i, rbal_date in enumerate(dates[:-1]):
                next_rbal = dates[i + 1]
                date_str = rbal_date.strftime("%Y%m%d")

                # Get KOSPI200 constituents and their market caps
                try:
                    caps = stock.get_market_cap_by_ticker(date_str, market="KOSPI")
                    kospi200 = stock.get_index_portfolio_deposit_file(
                        KOSPI200_IDX, date_str
                    )
                    kospi200_caps = caps[caps.index.isin(kospi200)]
                    if kospi200_caps.empty:
                        continue
                    cap_col = "시가총액"
                    top = kospi200_caps.nlargest(top_n, cap_col).index.tolist()
                except Exception:
                    continue

                # Equal-weight daily returns for holding period
                start_str = rbal_date.strftime("%Y%m%d")
                end_str = next_rbal.strftime("%Y%m%d")
                period_rets: list[pd.Series] = []
                for tkr in top:
                    try:
                        df = stock.get_market_ohlcv_by_date(start_str, end_str, tkr)
                        if df.empty:
                            continue
                        col = "종가" if "종가" in df.columns else df.columns[3]
                        r = df[col].pct_change().dropna()
                        r.index = pd.to_datetime(r.index)
                        period_rets.append(r)
                    except Exception:
                        continue

                if period_rets:
                    combined = pd.concat(period_rets, axis=1).mean(axis=1)
                    portfolio_rets.append(combined)

            if not portfolio_rets:
                return self._flat_result()

            all_rets = pd.concat(portfolio_rets).sort_index()
            all_rets = all_rets[~all_rets.index.duplicated(keep="last")]
            equity = (1 + all_rets).cumprod() * self.initial_capital
            metrics = compute_portfolio_metrics(all_rets)

            return BacktestResult(
                start=self.start,
                end=self.end,
                equity_curve=equity,
                returns=all_rets,
                metrics=metrics,
                benchmark_returns=self._fetch_index_returns(),
            )
        except Exception as exc:
            log.warning("backtest.equal_weight_failed", error=str(exc))
            return self._flat_result()

    # ------------------------------------------------------------------
    # Transaction cost model
    # ------------------------------------------------------------------

    @staticmethod
    def apply_costs(gross_return: float, is_sell: bool = False) -> float:
        commission = float(get_setting("execution.commission_bps", 1.5)) / 10000
        slippage = float(get_setting("execution.slippage_bps", 15)) / 10000
        tax = float(get_setting("execution.tax_sell_bps", 23)) / 10000 if is_sell else 0.0
        return gross_return - commission - slippage - tax

    # ------------------------------------------------------------------
    # Performance report
    # ------------------------------------------------------------------

    @staticmethod
    def print_report(result: BacktestResult) -> None:
        m = result.metrics
        print(f"\n{'='*50}")
        print(f"  Backtest: {result.start} → {result.end}")
        print(f"{'='*50}")
        print(f"  CAGR           : {m.cagr*100:>7.2f}%")
        print(f"  Volatility     : {m.volatility*100:>7.2f}%")
        print(f"  Sharpe         : {m.sharpe:>7.3f}")
        print(f"  Sortino        : {m.sortino:>7.3f}")
        print(f"  Max Drawdown   : {m.max_drawdown*100:>7.2f}%")
        print(f"  Calmar         : {m.calmar:>7.3f}")
        print(f"  VaR (95%)      : {m.var_95*100:>7.2f}%")
        print(f"  Hit Rate       : {m.hit_ratio*100:>7.2f}%")
        print(f"{'='*50}\n")


def main() -> None:
    import typer

    app = typer.Typer(add_completion=False)

    @app.command()
    def run(
        start: str = typer.Option("2015-01-01", help="Start date YYYY-MM-DD"),
        end: str = typer.Option(None, help="End date YYYY-MM-DD (default: today)"),
        mode: str = typer.Option("index", help="index | equal_weight"),
        top_n: int = typer.Option(30, help="Top-N stocks for equal_weight mode"),
    ) -> None:
        from common.logging import setup_logging
        setup_logging()
        eng = BacktestEngine(
            start=date.fromisoformat(start),
            end=date.fromisoformat(end) if end else None,
        )
        if mode == "equal_weight":
            result = eng.run_equal_weight(top_n=top_n)
        else:
            result = eng.run()
        BacktestEngine.print_report(result)

    app()


if __name__ == "__main__":
    main()
