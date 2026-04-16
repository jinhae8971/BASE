"""Pydantic models shared across the pipeline."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StockMarket(BaseModel):
    ticker: str
    name: str
    sector: str
    close: float
    open: float = 0
    high: float = 0
    low: float = 0
    volume: int = 0
    trading_value: float = 0
    market_cap: float = 0
    change_pct: float = 0


class StockSnapshot(BaseModel):
    ticker: str
    name: str
    close: float
    market_cap: float
    trading_value: float


class DailySnapshot(BaseModel):
    date: str
    fetched_at: datetime
    stocks: list[StockSnapshot]


class GainerStock(BaseModel):
    ticker: str
    name: str
    sector: str
    close: float
    market_cap: float
    trading_value: float
    volume: int = 0
    change_pct_1d: float
    change_pct_nd: float
    price_n_days_ago: float | None = None
    market_cap_rank: int | None = None


class NewsItem(BaseModel):
    title: str
    url: str
    source: str | None = None
    published_at: str | None = None


class StockAnalysis(BaseModel):
    ticker: str
    name: str
    pump_thesis: str
    drivers: list[str]
    risks: list[str]
    sector_tags: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    news_used: list[NewsItem] = Field(default_factory=list)


class NarrativeInsight(BaseModel):
    current_narrative: str
    hot_sectors: list[str]
    cooling_sectors: list[str]
    investment_insight: str
    week_over_week_change: str


class DailyReport(BaseModel):
    date: str
    generated_at: datetime
    gainers: list[GainerStock]
    analyses: list[StockAnalysis]
    narrative: NarrativeInsight

    @property
    def narrative_tagline(self) -> str:
        return self.narrative.current_narrative[:140]
