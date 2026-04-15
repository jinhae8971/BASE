"""Binance spot client facade.

`BinanceClient` is the one type `DailyWorkflow` and the test suite
construct directly. Internally it dispatches by `TradingMode`:

  - DRY   : returns deterministic stub fills; no network I/O.
  - PAPER : delegates to `BinanceLiveClient` against Binance testnet.
  - LIVE  : delegates to `BinanceLiveClient` against Binance prod, and
            every operation is gated by `live_gate.assert_live_allowed()`.

Paper and live share the same authenticated client class; only the base
URL and API keys differ. That is intentional -- we want whatever bugs we
find in paper to also have been latent in live, so the paper run is as
production-identical as we can make it.

Tests can inject a pre-built `live_client` (typically with an
`httpx.MockTransport`) to exercise the non-dry branches without
touching the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config import TradingMode, get_settings
from src.execution.killswitch import is_halted
from src.logging import get_logger

if TYPE_CHECKING:  # avoid circular import at runtime
    from src.execution.binance_live_client import BinanceLiveClient

log = get_logger("binance")

PEAK_STATE_FILE = Path("data/state/peak_equity.json")


def _load_peak_state() -> float:
    try:
        if PEAK_STATE_FILE.exists():
            data = json.loads(PEAK_STATE_FILE.read_text(encoding="utf-8"))
            return float(data.get("peak_equity_usd", 0.0) or 0.0)
    except Exception as exc:  # noqa: BLE001
        log.warning("binance.peak_load_failed", err=str(exc))
    return 0.0


def _save_peak_state(peak: float) -> None:
    try:
        PEAK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PEAK_STATE_FILE.write_text(
            json.dumps({"peak_equity_usd": peak}),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("binance.peak_save_failed", err=str(exc))


@dataclass
class Order:
    symbol: str
    side: str  # BUY | SELL
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
    def __init__(self, live_client: "BinanceLiveClient | None" = None) -> None:
        self.settings = get_settings()
        self._live: BinanceLiveClient | None = live_client

    async def submit(self, order: Order) -> Fill:
        if is_halted():
            raise RuntimeError("HALT file present -- refusing to submit order")
        mode = self.settings.trading_mode
        if mode == TradingMode.DRY:
            return self._dry_fill(order)
        client = self._get_live_client()
        return await client.submit(order)

    async def account_equity_usd(self) -> float:
        mode = self.settings.trading_mode
        if mode == TradingMode.DRY:
            return self.settings.initial_capital_usdt
        return await self._get_live_client().account_equity_usd()

    async def peak_equity_usd(self) -> float:
        """Running high-water mark of account equity.

        Used by the MDD circuit breaker to compute drawdown against the
        all-time peak, not against the current tick. In dry mode we
        return the configured initial capital (no drawdown ever). In
        paper/live, we persist the peak to `data/state/peak_equity.json`
        across process restarts so the circuit breaker survives a
        scheduler restart during a drawdown.
        """
        mode = self.settings.trading_mode
        current = await self.account_equity_usd()
        if mode == TradingMode.DRY:
            return max(current, self.settings.initial_capital_usdt)
        persisted = _load_peak_state()
        peak = max(persisted, current)
        if peak > persisted:
            _save_peak_state(peak)
        return peak

    # --- lazy live client factory -------------------------------------

    def _get_live_client(self) -> "BinanceLiveClient":
        if self._live is None:
            from src.execution.binance_live_client import (
                LIVE_BASE,
                PAPER_BASE,
                BinanceLiveClient,
            )

            base_url = (
                LIVE_BASE
                if self.settings.trading_mode == TradingMode.LIVE
                else PAPER_BASE
            )
            if not self.settings.binance_api_key or not self.settings.binance_api_secret:
                raise RuntimeError(
                    f"{self.settings.trading_mode.value} mode requires "
                    "BINANCE_API_KEY and BINANCE_API_SECRET"
                )
            self._live = BinanceLiveClient(
                base_url=base_url,
                api_key=self.settings.binance_api_key,
                api_secret=self.settings.binance_api_secret,
            )
        return self._live

    # --- dry-mode stub fill -------------------------------------------

    def _dry_fill(self, order: Order) -> Fill:
        log.info("binance.dry_fill", **order.__dict__)
        return Fill(
            symbol=order.symbol,
            side=order.side,
            qty=order.qty_usd / 100.0,  # assume $100/unit for dry run
            price=100.0,
            fee_usd=order.qty_usd * 0.001,
            filled_at=datetime.now(UTC),
        )
