"""Position tracker -- open/close lifecycle with realized P&L.

Observes fills from the orchestrator and maintains per-symbol open-position
state (quantity + average entry price + entry-time agent decisions).
When a SELL fully closes a position, emits a `ClosedPosition` event that
the `LearningLoop` consumes to run reflection and update ELO.

This is deliberately a separate layer from the Binance client so it works
identically for the simulated client (backtest), testnet (paper), and live
exchange -- all of which produce the same `Fill` shape.

Partial exits update realized P&L but do NOT trigger reflection. The
"thesis was right / wrong" question only gets a verdict when the whole
position is closed out and the clock stops on the holding period.

Weighted-average cost basis with a **first-entry-wins** context rule: if
you add to a winning position, the reflection still judges the *original*
thesis (not the pyramid add). Phase 6.5 can revisit this if we introduce
scale-ins as a first-class concept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class EntryContext:
    """Everything the reflection agent needs to audit the original thesis."""

    run_id: str
    as_of: datetime
    agent_payloads: dict[str, dict[str, Any]]
    macro_regime: str
    universe: list[str]


@dataclass
class OpenPosition:
    symbol: str
    qty: float
    avg_entry_price: float
    opened_at: datetime
    entry: EntryContext
    realized_pnl_usd: float = 0.0


@dataclass
class ClosedPosition:
    symbol: str
    avg_entry_price: float
    exit_price: float
    total_qty: float
    opened_at: datetime
    closed_at: datetime
    realized_pnl_usd: float
    entry: EntryContext

    @property
    def holding_period_s(self) -> int:
        return int((self.closed_at - self.opened_at).total_seconds())

    @property
    def holding_period_days(self) -> float:
        return self.holding_period_s / 86_400

    @property
    def realized_pnl_pct(self) -> float:
        if self.avg_entry_price <= 0:
            return 0.0
        return 100.0 * (self.exit_price / self.avg_entry_price - 1.0)


class PositionTracker:
    def __init__(self) -> None:
        self.open_positions: dict[str, OpenPosition] = {}

    # --- observation API ---------------------------------------------

    def on_buy(
        self,
        symbol: str,
        qty: float,
        price: float,
        at: datetime,
        entry: EntryContext,
    ) -> None:
        existing = self.open_positions.get(symbol)
        if existing is None:
            self.open_positions[symbol] = OpenPosition(
                symbol=symbol,
                qty=qty,
                avg_entry_price=price,
                opened_at=at,
                entry=entry,
            )
            return

        new_qty = existing.qty + qty
        if new_qty <= 0:
            # Degenerate (shouldn't happen with positive qty inputs).
            del self.open_positions[symbol]
            return
        existing.avg_entry_price = (
            existing.qty * existing.avg_entry_price + qty * price
        ) / new_qty
        existing.qty = new_qty
        # First-entry-wins: keep existing.entry unchanged.

    def on_sell(
        self,
        symbol: str,
        qty: float,
        price: float,
        at: datetime,
    ) -> ClosedPosition | None:
        pos = self.open_positions.get(symbol)
        if pos is None:
            return None

        sold = min(qty, pos.qty)
        realized_on_this_slice = (price - pos.avg_entry_price) * sold
        pos.realized_pnl_usd += realized_on_this_slice

        remaining = pos.qty - sold
        if remaining > 1e-12:
            pos.qty = remaining
            return None  # partial exit; no reflection yet

        closed = ClosedPosition(
            symbol=symbol,
            avg_entry_price=pos.avg_entry_price,
            exit_price=price,
            total_qty=sold,
            opened_at=pos.opened_at,
            closed_at=at,
            realized_pnl_usd=pos.realized_pnl_usd,
            entry=pos.entry,
        )
        del self.open_positions[symbol]
        return closed

    # --- introspection -----------------------------------------------

    def is_open(self, symbol: str) -> bool:
        return symbol in self.open_positions

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            sym: {
                "qty": pos.qty,
                "avg_entry_price": pos.avg_entry_price,
                "opened_at": pos.opened_at.isoformat(),
                "realized_pnl_usd": pos.realized_pnl_usd,
            }
            for sym, pos in self.open_positions.items()
        }
