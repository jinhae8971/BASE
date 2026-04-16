"""Tests for the 48h gainer ranker."""
from __future__ import annotations

from datetime import UTC, datetime

from src.models import CoinMarket, CoinSnapshot, DailySnapshot
from src.ranker import rank_top_gainers


def _market(**kwargs) -> CoinMarket:
    defaults = dict(
        id="x",
        symbol="x",
        name="X",
        image=None,
        current_price=100.0,
        market_cap=10_000_000.0,
        market_cap_rank=50,
        total_volume=5_000_000.0,
        price_change_percentage_24h=5.0,
    )
    defaults.update(kwargs)
    return CoinMarket(**defaults)


def _snapshot(coins: list[CoinSnapshot]) -> DailySnapshot:
    return DailySnapshot(date="2026-04-14", fetched_at=datetime.now(UTC), coins=coins)


def test_ranker_picks_top_k_by_48h_change():
    markets = [
        _market(id="a", symbol="a", current_price=120.0),  # +20%
        _market(id="b", symbol="b", current_price=150.0),  # +50%
        _market(id="c", symbol="c", current_price=105.0),  # +5%
        _market(id="d", symbol="d", current_price=180.0),  # +80%
        _market(id="e", symbol="e", current_price=200.0),  # +100%
        _market(id="f", symbol="f", current_price=110.0),  # +10%
    ]
    prior = _snapshot([
        CoinSnapshot(id=m.id, symbol=m.symbol, price=100.0, market_cap=1e7, volume=5e6)
        for m in markets
    ])
    gainers = rank_top_gainers(markets, prior)
    assert [g.id for g in gainers] == ["e", "d", "b", "a", "f"]
    assert gainers[0].change_48h_pct == 100.0


def test_ranker_excludes_stablecoins_and_wrapped():
    markets = [
        _market(id="tether", symbol="usdt", current_price=101.0),
        _market(id="wrapped-bitcoin", symbol="wbtc", current_price=130.0),
        _market(id="real-coin", symbol="real", current_price=120.0),
    ]
    prior = _snapshot([
        CoinSnapshot(id=m.id, symbol=m.symbol, price=100.0, market_cap=1e7, volume=5e6)
        for m in markets
    ])
    gainers = rank_top_gainers(markets, prior)
    ids = {g.id for g in gainers}
    assert "tether" not in ids
    assert "wrapped-bitcoin" not in ids
    assert "real-coin" in ids


def test_ranker_filters_low_volume():
    markets = [
        _market(id="thin", symbol="thin", current_price=200.0, total_volume=100.0),
        _market(id="thick", symbol="thick", current_price=120.0, total_volume=5e6),
    ]
    prior = _snapshot([
        CoinSnapshot(id="thin", symbol="thin", price=100.0, market_cap=1e7, volume=100.0),
        CoinSnapshot(id="thick", symbol="thick", price=100.0, market_cap=1e7, volume=5e6),
    ])
    gainers = rank_top_gainers(markets, prior)
    assert {g.id for g in gainers} == {"thick"}


def test_ranker_fallback_uses_24h_when_no_snapshot():
    markets = [
        _market(id="a", symbol="a", price_change_percentage_24h=30.0),
        _market(id="b", symbol="b", price_change_percentage_24h=-5.0),
        _market(id="c", symbol="c", price_change_percentage_24h=10.0),
    ]
    gainers = rank_top_gainers(markets, None)
    ids = [g.id for g in gainers]
    assert ids[0] == "a"
    assert "b" not in ids  # negative filtered out


def test_ranker_excludes_negative_and_zero():
    markets = [
        _market(id="up", symbol="up", current_price=110.0),
        _market(id="flat", symbol="flat", current_price=100.0),
        _market(id="down", symbol="down", current_price=90.0),
    ]
    prior = _snapshot([
        CoinSnapshot(id=m.id, symbol=m.symbol, price=100.0, market_cap=1e7, volume=5e6)
        for m in markets
    ])
    gainers = rank_top_gainers(markets, prior)
    assert [g.id for g in gainers] == ["up"]
