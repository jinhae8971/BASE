"""Tests for the N-trading-day gainer ranker."""
from __future__ import annotations

from datetime import UTC, datetime

from src.models import DailySnapshot, StockMarket, StockSnapshot
from src.ranker import rank_top_gainers


def _stock(**kwargs) -> StockMarket:
    defaults = dict(
        ticker="005930",
        name="삼성전자",
        market="KOSPI",
        close=70000,
        open=69000,
        high=71000,
        low=68000,
        volume=10_000_000,
        trading_value=5_000_000_000,
        market_cap=400_000_000_000_000,
        change_pct=3.0,
    )
    defaults.update(kwargs)
    return StockMarket(**defaults)


def _snapshot(stocks: list[StockSnapshot]) -> DailySnapshot:
    return DailySnapshot(date="2026-04-14", fetched_at=datetime.now(UTC), stocks=stocks)


def test_ranker_picks_top_k_by_nd_change():
    stocks = [
        _stock(ticker="A", name="A주식", close=120),   # +20%
        _stock(ticker="B", name="B주식", close=150),   # +50%
        _stock(ticker="C", name="C주식", close=105),   # +5%
        _stock(ticker="D", name="D주식", close=180),   # +80%
        _stock(ticker="E", name="E주식", close=200),   # +100%
        _stock(ticker="F", name="F주식", close=110),   # +10%
    ]
    prior = _snapshot([
        StockSnapshot(ticker=s.ticker, name=s.name, close=100, market_cap=1e14, trading_value=5e9)
        for s in stocks
    ])
    gainers = rank_top_gainers(stocks, prior)
    assert [g.ticker for g in gainers] == ["E", "D", "B", "A", "F"]
    assert gainers[0].change_pct_nd == 100.0


def test_ranker_filters_low_volume():
    stocks = [
        _stock(ticker="THIN", name="저유동", close=200, trading_value=100),
        _stock(ticker="THICK", name="고유동", close=120, trading_value=5e9),
    ]
    prior = _snapshot([
        StockSnapshot(ticker="THIN", name="저유동", close=100, market_cap=1e14, trading_value=100),
        StockSnapshot(ticker="THICK", name="고유동", close=100, market_cap=1e14, trading_value=5e9),
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
    assert "B" not in tickers  # negative filtered out


def test_ranker_excludes_negative_and_zero():
    stocks = [
        _stock(ticker="UP", name="상승", close=110),
        _stock(ticker="FLAT", name="보합", close=100),
        _stock(ticker="DOWN", name="하락", close=90),
    ]
    prior = _snapshot([
        StockSnapshot(ticker=s.ticker, name=s.name, close=100, market_cap=1e14, trading_value=5e9)
        for s in stocks
    ])
    gainers = rank_top_gainers(stocks, prior)
    assert [g.ticker for g in gainers] == ["UP"]
