"""Trade journal -- source of truth for learning and reporting.

Phase 0 defines the schemas as dataclasses and provides an in-memory store
so the orchestrator can run end-to-end without Postgres. Phase 1 will swap
the backend for SQLAlchemy + TimescaleDB hypertables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class AgentDecision:
    agent: str
    model: str
    payload: dict[str, Any]
    latency_ms: int


@dataclass
class TradeRecord:
    run_id: str
    ts: datetime
    symbol: str
    side: str              # BUY | SELL
    qty: float
    price: float
    notional_usd: float
    agent_decisions: list[AgentDecision]
    portfolio_snapshot: dict[str, Any]
    macro_regime: str
    realized_pnl_usd: float | None = None
    holding_period_s: int | None = None
    attribution: dict[str, float] = field(default_factory=dict)


class InMemoryTradeStore:
    def __init__(self) -> None:
        self._trades: list[TradeRecord] = []

    async def insert(self, trade: TradeRecord) -> None:
        self._trades.append(trade)

    async def recent(self, n: int = 100) -> list[TradeRecord]:
        return self._trades[-n:]

    async def equity_curve(self) -> list[tuple[datetime, float]]:
        cum = 0.0
        curve: list[tuple[datetime, float]] = []
        for t in self._trades:
            if t.realized_pnl_usd is not None:
                cum += t.realized_pnl_usd
                curve.append((t.ts, cum))
        if not curve:
            curve.append((datetime.now(UTC), 0.0))
        return curve
