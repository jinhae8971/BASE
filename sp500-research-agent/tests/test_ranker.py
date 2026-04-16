"""Tests for the N-trading-day gainer ranker."""
from __future__ import annotations

from datetime import UTC, datetime

from src.models import DailySnapshot, StockMarket, StockSnapshot
from src.ranker import rank_top_gainers


def _stock(**kwargs) -> StockMarket:
    defaults = dict(
        ticker="AAPL",
        name="Apple Inc.",
        sector="Technology",
        sub_industry="Technology Hardware",
        close=180.0,
        open=178.0,
        high=182.0,
        low=177.0,
        volume=50_000_000,
        trading_value=9_000_000_000.0,
        market_cap=2_800_000_000_000.0,
        change_pct=2.0,
    )
    defaults.update(kwargs)
    return StockMarket(**defaults)


def _snapshot(stocks: list[StockSnapshot]) -> DailySnapshot:
    return DailySnapshot(date="2026-04-14", fetched_at=datetime.now(UTC), stocks=stocks)


def test_ranker_picks_top_k_by_nd_change():
    stocks = [
        _stock(ticker="A", name="A Corp", close=120),
        _stock(ticker="B", name="B Corp", close=150),
        _stock(ticker="C", name="C Corp", close=105),
        _stock(ticker="D", name="D Corp", close=180),
        _stock(ticker="E", name="E Corp", close=200),
        _stock(ticker="F", name="F Corp", close=110),
    ]
    prior = _snapshot([
        StockSnapshot(ticker=s.ticker, name=s.name, close=100, market_cap=1e12, trading_value=9e9)
        for s in stocks
    ])
    gainers = rank_top_gainers(stocks, prior)
    assert [g.ticker for g in gainers] == ["E", "D", "B", "A", "F"]
    assert gainers[0].change_pct_nd == 100.0


def test_ranker_filters_low_volume():
    stocks = [
        _stock(ticker="THIN", name="Thin Corp", close=200, trading_value=100),
        _stock(ticker="THICK", name="Thick Corp", close=120, trading_value=9e9),
    ]
    prior = _snapshot([
        StockSnapshot(ticker="THIN", name="Thin Corp", close=100, market_cap=1e12, trading_value=100),
        StockSnapshot(ticker="THICK", name="Thick Corp", close=100, market_cap=1e12, trading_value=9e9),
    ])
    gainers = rank_top_gainers(stocks, prior)
    assert {g.ticker for g in gainers} == {"THICK"}


def test_ranker_fallback_uses_1d_when_no_snapshot():
    stocks = [
        _stock(ticker="A", name="A", change_pct=10.0),
        _stock(ticker="B", name="B", change_pct=-5.0),
        _stock(ticker="C", name="C", change_pct=3.0),
    ]
    gainers = rank_top_gainers(stocks, None)
    tickers = [g.ticker for g in gainers]
    assert tickers[0] == "A"
    assert "B" not in tickers


def test_ranker_excludes_negative_and_zero():
    stocks = [
        _stock(ticker="UP", name="Up", close=110),
        _stock(ticker="FLAT", name="Flat", close=100),
        _stock(ticker="DOWN", name="Down", close=90),
    ]
    prior = _snapshot([
        StockSnapshot(ticker=s.ticker, name=s.name, close=100, market_cap=1e12, trading_value=9e9)
        for s in stocks
    ])
    gainers = rank_top_gainers(stocks, prior)
    assert [g.ticker for g in gainers] == ["UP"]
