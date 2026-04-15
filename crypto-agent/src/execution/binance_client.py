"""Binance spot client wrapper.

Three modes:
  - dry:   no network, no orders. Returns stub fills.
  - paper: Binance testnet (real API path, fake money).
  - live:  real Binance spot. Key must have Withdraw OFF and Futures OFF.

Phase 0 implements only the dry path so the orchestrator can run end-to-end.
Phase 1 wires python-binance for paper, Phase 5 flips the live switch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from src.config import TradingMode, get_settings
from src.execution.killswitch import is_halted
from src.logging import get_logger

log = get_logger("binance")


@dataclass
class Order:
    symbol: str
    side: str          # BUY | SELL
    qty_usd: float
    type: str = "MARKET"
    limit_price: float | None = None


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float
    price: float
    fee_usd: float
    filled_at: datetime


class BinanceClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def submit(self, order: Order) -> Fill:
        if is_halted():
            raise RuntimeError("HALT file present — refusing to submit order")

        mode = self.settings.trading_mode
        if mode == TradingMode.DRY:
            log.info("binance.dry_fill", **order.__dict__)
            return Fill(
                symbol=order.symbol,
                side=order.side,
                qty=order.qty_usd / 100.0,  # assume $100/unit for dry run
                price=100.0,
                fee_usd=order.qty_usd * 0.001,
                filled_at=datetime.now(UTC),
            )
        raise NotImplementedError(f"{mode} mode is implemented in Phase 1+")

    async def account_equity_usd(self) -> float:
        if self.settings.trading_mode == TradingMode.DRY:
            return self.settings.initial_capital_usdt
        raise NotImplementedError
