"""Pydantic models shared across the pipeline."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CoinMarket(BaseModel):
    """Raw market data for one coin from CoinGecko /coins/markets."""

    id: str
    symbol: str
    name: str
    image: str | None = None
    current_price: float
    market_cap: float
    market_cap_rank: int | None = None
    total_volume: float
    price_change_percentage_24h: float | None = None


class CoinSnapshot(BaseModel):
    """Minimal fields stored daily for 48h change computation."""

    id: str
    symbol: str
    price: float
    market_cap: float
    volume: float


class DailySnapshot(BaseModel):
    date: str  # YYYY-MM-DD (UTC)
    fetched_at: datetime
    coins: list[CoinSnapshot]


class GainerCoin(BaseModel):
    """A ranked 48h gainer with the raw market context."""

    id: str
    symbol: str
    name: str
    image: str | None = None
    current_price: float
    market_cap: float
    market_cap_rank: int | None = None
    total_volume: float
    change_24h_pct: float | None = None
    change_48h_pct: float
    price_48h_ago: float | None = None


class NewsItem(BaseModel):
    title: str
    url: str
    source: str | None = None
    published_at: str | None = None


class CoinAnalysis(BaseModel):
    """Claude analysis result per coin."""

    coin_id: str
    symbol: str
    pump_thesis: str
    drivers: list[str]
    risks: list[str]
    category_tags: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    news_used: list[NewsItem] = Field(default_factory=list)


class NarrativeInsight(BaseModel):
    current_narrative: str
    hot_sectors: list[str]
    cooling_sectors: list[str]
    investment_insight: str
    week_over_week_change: str


class DailyReport(BaseModel):
    date: str  # YYYY-MM-DD (UTC)
    generated_at: datetime
    gainers: list[GainerCoin]
    analyses: list[CoinAnalysis]
    narrative: NarrativeInsight

    @property
    def narrative_tagline(self) -> str:
        return self.narrative.current_narrative[:140]
