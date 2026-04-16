"""Pydantic models shared across the pipeline."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StockMarket(BaseModel):
    """Market data for one KRX-listed stock."""

    ticker: str        # 6-digit code, e.g. "005930"
    name: str          # 종목명, e.g. "삼성전자"
    market: str        # KOSPI | KOSDAQ
    close: float       # 종가 (KRW)
    open: float = 0
    high: float = 0
    low: float = 0
    volume: int = 0    # 거래량 (주)
    trading_value: float = 0  # 거래대금 (KRW)
    market_cap: float = 0     # 시가총액 (KRW)
    change_pct: float = 0     # 전일 대비 등락률 (%)


class StockSnapshot(BaseModel):
    """Minimal fields stored daily for N-day change computation."""

    ticker: str
    name: str
    close: float
    market_cap: float
    trading_value: float


class DailySnapshot(BaseModel):
    date: str  # YYYY-MM-DD (KST, trading date)
    fetched_at: datetime
    stocks: list[StockSnapshot]


class GainerStock(BaseModel):
    """A ranked N-trading-day gainer."""

    ticker: str
    name: str
    market: str
    close: float
    market_cap: float
    trading_value: float
    volume: int = 0
    change_pct_1d: float   # 전일 대비
    change_pct_nd: float   # N거래일 대비
    price_n_days_ago: float | None = None
    market_cap_rank: int | None = None


class NewsItem(BaseModel):
    title: str
    url: str
    source: str | None = None
    published_at: str | None = None


class StockAnalysis(BaseModel):
    """Claude analysis result per stock."""

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
    date: str  # YYYY-MM-DD (trading date)
    generated_at: datetime
    market_type: str  # KOSPI | KOSDAQ | ALL
    gainers: list[GainerStock]
    analyses: list[StockAnalysis]
    narrative: NarrativeInsight

    @property
    def narrative_tagline(self) -> str:
        return self.narrative.current_narrative[:140]
