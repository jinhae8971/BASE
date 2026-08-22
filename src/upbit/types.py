"""Domain models for the Upbit day-trading subsystem.

These are intentionally separate from `common.types` (which models the Korean
*equity* side): crypto trades 24/7, positions are fractional, and the score
breakdown is a first-class artefact surfaced in the dashboard.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

OrderSide = Literal["bid", "ask"]
TradeMode = Literal["paper", "live"]


class Regime(StrEnum):
    """BTC-driven market regime; gates whether new risk may be taken on."""

    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


class ExitReason(StrEnum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    TIME_EXIT = "time_exit"
    REGIME_EXIT = "regime_exit"
    DAILY_LOSS_KILL = "daily_loss_kill"
    MANUAL = "manual"
    PANIC = "panic"
    # Released from engine management rather than sold: the coin became a
    # 장기보유 holding, or its balance is no longer on the exchange.
    LONG_TERM_PROTECTED = "long_term_protected"
    BALANCE_MISSING = "balance_missing"


class ScoreBreakdown(BaseModel):
    """Per-pillar detail behind a candidate's composite score (0-100 each)."""

    volume: float = 0.0
    flow: float = 0.0
    technical: float = 0.0
    beta: float = 0.0
    total: float = 0.0
    metrics: dict[str, float] = Field(default_factory=dict)


class Candidate(BaseModel):
    """One scored coin from a morning scan."""

    market: str                       # e.g. "KRW-XRP"
    symbol: str                       # e.g. "XRP"
    korean_name: str | None = None
    price: float = 0.0
    change_rate_24h: float = 0.0
    trade_price_24h: float = 0.0      # 24h 거래대금 (KRW)
    score: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    rank: int | None = None
    selected: bool = False
    reason: str = ""


class MarketRegimeView(BaseModel):
    """Top-level BTC regime read, computed once per scan."""

    regime: Regime = Regime.NEUTRAL
    exposure_multiplier: float = 1.0
    btc_price: float = 0.0
    btc_return_7d: float = 0.0
    btc_rsi: float = 50.0
    above_ma20: bool = False
    above_ma50: bool = False
    rationale: str = ""


class Position(BaseModel):
    """An engine-managed day-trading position (long-term holdings excluded)."""

    market: str
    symbol: str
    volume: float
    avg_price: float
    opened_at: datetime
    high_water: float = 0.0           # best price seen, drives the trailing stop
    stop_price: float = 0.0
    take_price: float = 0.0
    mode: TradeMode = "paper"
    run_id: int | None = None
    status: Literal["open", "closed"] = "open"
    closed_at: datetime | None = None
    exit_reason: str | None = None

    @property
    def cost_krw(self) -> float:
        return self.volume * self.avg_price


class OrderRequest(BaseModel):
    market: str
    side: OrderSide
    ord_type: Literal["limit", "price", "market"] = "market"
    volume: float | None = None       # required for ask/limit
    price: float | None = None        # KRW amount for `price`, unit price for `limit`
    reason: str = ""


class OrderResult(BaseModel):
    request: OrderRequest
    uuid: str | None = None
    state: Literal["submitted", "done", "cancelled", "rejected", "simulated"] = "submitted"
    executed_volume: float = 0.0
    avg_price: float = 0.0
    paid_fee: float = 0.0
    krw_amount: float = 0.0
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    message: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class LongTermHolding(BaseModel):
    """A coin the user holds long-term: never bought, never sold by the engine."""

    symbol: str
    locked_quantity: float = 0.0      # 0 == the entire balance is protected
    memo: str = ""
