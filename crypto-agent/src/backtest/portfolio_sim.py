"""Simulated Binance spot client for the backtest engine.

Implements the same surface as `execution.binance_client.BinanceClient` so
`DailyWorkflow` can be driven end-to-end (agents, optimizer, risk guards,
trade journal) without any network. The engine sets `current_prices` and
`current_day` before each `wf.run()` call; `submit()` then fills orders at
the current-day close price with a configurable fee and slippage.

Accounting model: spot only, long-only, USDT-quoted pairs, fractional
quantities permitted. Holdings are tracked as base-asset quantity; equity is
`cash + Σ qty_i * price_i` where prices come from the engine's daily feed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.execution.binance_client import Fill, Order
from src.logging import get_logger

log = get_logger("backtest.sim")

DEFAULT_FEE_BPS = 10.0      # 0.10% per side (Binance spot default)
DEFAULT_SLIPPAGE_BPS = 5.0  # 0.05% each way


@dataclass
class SimConfig:
    initial_cash: float = 1000.0
    fee_bps: float = DEFAULT_FEE_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS


@dataclass
class PortfolioState:
    cash: float
    positions: dict[str, float] = field(default_factory=dict)   # symbol -> qty
    cost_basis: dict[str, float] = field(default_factory=dict)  # symbol -> avg USD/unit

    def equity(self, prices: dict[str, float]) -> float:
        total = self.cash
        for sym, qty in self.positions.items():
            total += qty * prices.get(sym, 0.0)
        return total

    def weights(self, prices: dict[str, float]) -> dict[str, float]:
        eq = self.equity(prices)
        if eq <= 0:
            return {}
        return {sym: (qty * prices.get(sym, 0.0)) / eq for sym, qty in self.positions.items()}


class SimulatedBinanceClient:
    """Drop-in replacement for `BinanceClient` inside a backtest loop."""

    def __init__(self, config: SimConfig | None = None) -> None:
        self.config = config or SimConfig()
        self.state = PortfolioState(cash=self.config.initial_cash)
        self.current_prices: dict[str, float] = {}
        self.current_day: datetime = datetime.now(UTC)
        self.trade_log: list[Fill] = []

    # --- engine hooks -------------------------------------------------

    def advance(self, day: datetime, prices: dict[str, float]) -> None:
        """Called by the engine before each workflow run."""
        self.current_day = day
        self.current_prices = prices

    def mark_equity(self) -> float:
        return self.state.equity(self.current_prices)

    # --- BinanceClient surface ---------------------------------------

    async def submit(self, order: Order) -> Fill:
        price = self.current_prices.get(order.symbol)
        if not price or price <= 0:
            raise RuntimeError(f"sim: no price for {order.symbol} on {self.current_day}")

        fee_mult = self.config.fee_bps / 10_000
        slip_mult = self.config.slippage_bps / 10_000

        if order.side == "BUY":
            exec_price = price * (1 + slip_mult)
            gross = order.qty_usd
            fee = gross * fee_mult
            net_for_qty = gross - fee
            qty = net_for_qty / exec_price
            if gross > self.state.cash + 1e-9:
                # Partial fill up to available cash.
                gross = self.state.cash
                fee = gross * fee_mult
                qty = (gross - fee) / exec_price
                if qty <= 0:
                    raise RuntimeError("sim: insufficient cash for BUY")
            self.state.cash -= gross
            old_qty = self.state.positions.get(order.symbol, 0.0)
            old_basis = self.state.cost_basis.get(order.symbol, 0.0)
            new_qty = old_qty + qty
            self.state.cost_basis[order.symbol] = (
                (old_qty * old_basis + qty * exec_price) / new_qty if new_qty > 0 else 0.0
            )
            self.state.positions[order.symbol] = new_qty
            fill = Fill(order.symbol, "BUY", qty, exec_price, fee, self.current_day)
        else:  # SELL
            exec_price = price * (1 - slip_mult)
            held = self.state.positions.get(order.symbol, 0.0)
            if held <= 0:
                raise RuntimeError(f"sim: cannot SELL {order.symbol} — no position")
            # qty_usd is notional desired; clamp to held value.
            desired_qty = min(held, order.qty_usd / exec_price)
            proceeds = desired_qty * exec_price
            fee = proceeds * fee_mult
            self.state.cash += proceeds - fee
            remaining = held - desired_qty
            if remaining <= 1e-12:
                self.state.positions.pop(order.symbol, None)
                self.state.cost_basis.pop(order.symbol, None)
            else:
                self.state.positions[order.symbol] = remaining
            fill = Fill(order.symbol, "SELL", desired_qty, exec_price, fee, self.current_day)

        self.trade_log.append(fill)
        log.debug(
            "sim.fill",
            day=self.current_day.date().isoformat(),
            symbol=order.symbol,
            side=order.side,
            qty=round(fill.qty, 6),
            price=round(exec_price, 4),
            cash_after=round(self.state.cash, 2),
        )
        return fill

    async def account_equity_usd(self) -> float:
        return self.mark_equity()
