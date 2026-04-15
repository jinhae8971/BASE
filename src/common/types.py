"""Shared domain models (pydantic)."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class MarketRegime(str, Enum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


class AgentProposal(BaseModel):
    """Common output format for every specialist agent."""

    agent_name: str
    as_of: date
    conviction: int = Field(ge=0, le=10, description="0=low, 10=high")
    rationale: str
    # Aggregate-level view
    equity_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    regime: MarketRegime | None = None
    sector_tilts: dict[str, float] = Field(default_factory=dict)
    # Ticker-level view
    picks: list["TickerView"] = Field(default_factory=list)
    # Metadata
    context_used: dict = Field(default_factory=dict)


class TickerView(BaseModel):
    ticker: str
    name: str | None = None
    side: Side = Side.HOLD
    target_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    score: float | None = None
    rationale: str | None = None


class PortfolioTarget(BaseModel):
    """Output of the orchestrator after optimization."""

    as_of: date
    cash_weight: float
    positions: dict[str, float]  # ticker -> target weight
    expected_return: float | None = None
    expected_vol: float | None = None
    rationale: str = ""


class Order(BaseModel):
    ticker: str
    side: Side
    quantity: int
    price: float | None = None  # None = market
    order_type: Literal["limit", "market"] = "limit"


class ExecutionResult(BaseModel):
    order: Order
    submitted_at: datetime
    status: Literal["submitted", "filled", "partial", "rejected", "cancelled"]
    filled_qty: int = 0
    avg_price: float | None = None
    broker_order_id: str | None = None
    message: str = ""


class DecisionRecord(BaseModel):
    """Persisted entry in the Decision Journal."""

    id: str
    timestamp: datetime
    agent_name: str
    action: str
    ticker: str | None = None
    conviction: int
    rationale: str
    context: dict
    outcome_1w: float | None = None
    outcome_1m: float | None = None
    outcome_3m: float | None = None
