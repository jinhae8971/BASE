"""Tests for the N-trading-day gainer ranker."""
from __future__ import annotations

from datetime import UTC, datetime

from src.models import DailySnapshot, StockMarket, StockSnapshot
from src.ranker import rank_top_gainers


def _stock(**kwargs) -> StockMarket:
    defaults = dict(
        ticker="UNH", name="UnitedHealth Group", sector="Healthcare",
        close=500.0, open=495.0, high=510.0, low=490.0,
        volume=5_000_000, trading_value=2_500_000_000.0,
        market_cap=450_000_000_000.0, change_pct=2.0,
    )
    defaults.update(kwargs)
    return StockMarket(**defaults)


def _snapshot(stocks: list[StockSnapshot]) -> DailySnapshot:
    return DailySnapshot(date="2026-04-14", fetched_at=datetime.now(UTC), stocks=stocks)


def test_ranker_picks_top_k():
    stocks = [
        _stock(ticker="A", name="A", close=120),
        _stock(ticker="B", name="B", close=150),
        _stock(ticker="C", name="C", close=105),
        _stock(ticker="D", name="D", close=180),
        _stock(ticker="E", name="E", close=200),
        _stock(ticker="F", name="F", close=110),
    ]
    prior = _snapshot([
        StockSnapshot(ticker=s.ticker, name=s.name, close=100, market_cap=1e12, trading_value=2.5e9)
        for s in stocks
    ])
    gainers = rank_top_gainers(stocks, prior)
    assert [g.ticker for g in gainers] == ["E", "D", "B", "A", "F"]


def test_ranker_filters_low_volume():
    stocks = [
        _stock(ticker="THIN", close=200, trading_value=100),
        _stock(ticker="THICK", close=120, trading_value=2.5e9),
    ]
    prior = _snapshot([
        StockSnapshot(ticker="THIN", name="Thin", close=100, market_cap=1e12, trading_value=100),
        StockSnapshot(ticker="THICK", name="Thick", close=100, market_cap=1e12, trading_value=2.5e9),
    ])
    assert {g.ticker for g in rank_top_gainers(stocks, prior)} == {"THICK"}


def test_ranker_fallback():
    stocks = [_stock(ticker="A", change_pct=10.0), _stock(ticker="B", change_pct=-5.0)]
    gainers = rank_top_gainers(stocks, None)
    assert gainers[0].ticker == "A"
    assert "B" not in [g.ticker for g in gainers]


def test_ranker_excludes_negative():
    stocks = [_stock(ticker="UP", close=110), _stock(ticker="DOWN", close=90)]
    prior = _snapshot([
        StockSnapshot(ticker=s.ticker, name=s.ticker, close=100, market_cap=1e12, trading_value=2.5e9)
        for s in stocks
    ])
    assert [g.ticker for g in rank_top_gainers(stocks, prior)] == ["UP"]
