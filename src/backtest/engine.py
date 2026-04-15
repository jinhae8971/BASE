"""Event-driven backtest engine skeleton.

Phase 3 will fill in:
- Daily loop over historical trading days
- Re-play the full pipeline (agents → consensus → optimizer → execution)
  against a frozen view of data that existed on that date (no look-ahead)
- Transaction cost model (slippage, commission, tax)
- Performance report (metrics, equity curve, per-agent attribution)

For Phase 0 we provide a minimal interface so downstream modules can import
without errors, and `scripts/run_backtest.py` is wired up.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from common.config import get_setting
from common.logging import get_logger
from portfolio.risk import RiskMetrics, compute_portfolio_metrics

log = get_logger(__name__)


@dataclass
class BacktestResult:
    start: date
    end: date
    equity_curve: pd.Series
    returns: pd.Series
    metrics: RiskMetrics
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

    def run(self) -> BacktestResult:
        log.info("backtest.start", start=self.start, end=self.end)
        # Phase 0 stub: return a flat equity curve
        idx = pd.date_range(self.start, self.end, freq="B")
        returns = pd.Series(0.0, index=idx)
        equity = (1 + returns).cumprod() * self.initial_capital
        metrics = compute_portfolio_metrics(returns)
        return BacktestResult(
            start=self.start,
            end=self.end,
            equity_curve=equity,
            returns=returns,
            metrics=metrics,
        )


def main() -> None:
    import typer

    app = typer.Typer()

    @app.command()
    def run(
        start: str = "2015-01-01",
        end: str | None = None,
    ) -> None:
        eng = BacktestEngine(
            start=date.fromisoformat(start),
            end=date.fromisoformat(end) if end else None,
        )
        result = eng.run()
        typer.echo(f"Backtest: {result.start} → {result.end}")
        typer.echo(f"Metrics: {result.metrics}")

    app()


if __name__ == "__main__":
    main()
