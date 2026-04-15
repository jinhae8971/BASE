"""BacktestEngine: step DailyWorkflow over historical dates.

Each tick:
  1. Advance the SimulatedBinanceClient to the next trading day with that
     day's close prices.
  2. Run DailyWorkflow with a snapshot_fn that serves the historical
     snapshot up to that day.
  3. Record equity (mark-to-market at the new close).
  4. Record the BTC-HODL benchmark equity for the same day.

At the end, `compute_metrics()` turns the daily equity series into a
`PerformanceReport`. The engine never touches the network; everything runs
off the `HistoricalSnapshotProvider`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.backtest.metrics import PerformanceReport, compute_metrics
from src.backtest.portfolio_sim import SimConfig, SimulatedBinanceClient
from src.backtest.provider import HistoricalSnapshotProvider
from src.learning.elo import EloTable
from src.llm import LLMClient
from src.logging import get_logger
from src.memory.trade_store import InMemoryTradeStore
from src.orchestrator.daily_workflow import DailyWorkflow

log = get_logger("backtest.engine")


@dataclass
class BacktestResult:
    days: list[datetime]
    equity: list[float]
    btc_equity: list[float]
    gross_traded_usd: float
    report: PerformanceReport
    num_orders: int
    fills: list[Any] = field(default_factory=list)


class BacktestEngine:
    def __init__(
        self,
        provider: HistoricalSnapshotProvider,
        llm_client: LLMClient,
        sim_config: SimConfig | None = None,
        rebalance_every_n_days: int = 1,
    ) -> None:
        self.provider = provider
        self.sim = SimulatedBinanceClient(config=sim_config)
        self.llm_client = llm_client
        self.rebalance_every_n_days = max(1, rebalance_every_n_days)

    async def run(self) -> BacktestResult:
        days = self.provider.trading_days()
        if len(days) < 2:
            raise ValueError("backtest: need at least 2 warmup-adjusted days")

        equity_curve: list[float] = []
        btc_curve: list[float] = []
        gross_traded = 0.0
        num_orders = 0

        btc_day0_close = self.provider.btc_close(days[0])
        btc_units_held = (
            self.sim.config.initial_cash / btc_day0_close if btc_day0_close > 0 else 0.0
        )

        for i, day in enumerate(days):
            prices = self.provider.close_prices(day)
            self.sim.advance(day, prices)

            if i > 0 and i % self.rebalance_every_n_days == 0:
                result = await self._step(day)
                orders = result.get("orders") or []
                num_orders += len(orders)
                gross_traded += sum(float(o.get("qty_usd", 0.0)) for o in orders)

            eq = self.sim.mark_equity()
            equity_curve.append(eq)
            btc_curve.append(btc_units_held * prices.get("BTCUSDT", 0.0))

        report = compute_metrics(
            days=days,
            equity=equity_curve,
            btc_equity=btc_curve,
            gross_traded_usd=gross_traded,
        )
        log.info("backtest.done", **report.__dict__)
        return BacktestResult(
            days=days,
            equity=equity_curve,
            btc_equity=btc_curve,
            gross_traded_usd=gross_traded,
            report=report,
            num_orders=num_orders,
            fills=list(self.sim.trade_log),
        )

    async def _step(self, day: datetime) -> dict[str, Any]:
        wf = DailyWorkflow(
            elo=EloTable(),
            trade_store=InMemoryTradeStore(),
            binance=self.sim,  # type: ignore[arg-type]
            snapshot_fn=self.provider.snapshot_fn(day),
            llm_client=self.llm_client,
        )
        return await wf.run(universe_size=len(self.provider.universe))
