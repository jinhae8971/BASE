"""Binance public client tests backed by httpx MockTransport."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src.data.binance_md import BinanceMarketData
from src.data.cache import default_cache


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest.fixture(autouse=True)
async def _clear_cache():
    await default_cache().clear()
    yield
    await default_cache().clear()


def _handler_factory():
    old_onboard = _ms(datetime.now(UTC) - timedelta(days=365))
    new_onboard = _ms(datetime.now(UTC) - timedelta(days=5))

    exchange_info = {
        "symbols": [
            {"symbol": "BTCUSDT", "baseAsset": "BTC", "status": "TRADING", "onboardDate": old_onboard},
            {"symbol": "ETHUSDT", "baseAsset": "ETH", "status": "TRADING", "onboardDate": old_onboard},
            {"symbol": "SOLUSDT", "baseAsset": "SOL", "status": "TRADING", "onboardDate": old_onboard},
            {"symbol": "SHIBUSDT", "baseAsset": "SHIB", "status": "TRADING", "onboardDate": old_onboard},
            # Too young: should be filtered out.
            {"symbol": "NEWUSDT", "baseAsset": "NEW", "status": "TRADING", "onboardDate": new_onboard},
            # Halted: should be filtered out.
            {"symbol": "HALTUSDT", "baseAsset": "HALT", "status": "HALT", "onboardDate": old_onboard},
            # Stablecoin base: should be filtered out.
            {"symbol": "USDCUSDT", "baseAsset": "USDC", "status": "TRADING", "onboardDate": old_onboard},
            # Leveraged token suffix: should be filtered out.
            {"symbol": "BTCUPUSDT", "baseAsset": "BTCUP", "status": "TRADING", "onboardDate": old_onboard},
            # Non-USDT quote: should be filtered out.
            {"symbol": "ADABUSD", "baseAsset": "ADA", "status": "TRADING", "onboardDate": old_onboard},
        ]
    }

    tickers = [
        {"symbol": "BTCUSDT", "lastPrice": "60000", "priceChangePercent": "1.5", "quoteVolume": "2000000000"},
        {"symbol": "ETHUSDT", "lastPrice": "3000", "priceChangePercent": "1.2", "quoteVolume": "1000000000"},
        {"symbol": "SOLUSDT", "lastPrice": "150", "priceChangePercent": "2.0", "quoteVolume": "500000000"},
        # SHIB volume below $50M threshold -- should be filtered out.
        {"symbol": "SHIBUSDT", "lastPrice": "0.00001", "priceChangePercent": "0.5", "quoteVolume": "10000000"},
        {"symbol": "NEWUSDT", "lastPrice": "1", "priceChangePercent": "10.0", "quoteVolume": "900000000"},
        {"symbol": "HALTUSDT", "lastPrice": "1", "priceChangePercent": "0", "quoteVolume": "100000000"},
        {"symbol": "USDCUSDT", "lastPrice": "1", "priceChangePercent": "0", "quoteVolume": "5000000000"},
        {"symbol": "BTCUPUSDT", "lastPrice": "10", "priceChangePercent": "0", "quoteVolume": "600000000"},
        {"symbol": "ADABUSD", "lastPrice": "0.5", "priceChangePercent": "0", "quoteVolume": "700000000"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=exchange_info)
        if path == "/api/v3/ticker/24hr":
            return httpx.Response(200, json=tickers)
        if path == "/api/v3/klines":
            # One trivial candle row.
            now_ms = _ms(datetime.now(UTC))
            row = [now_ms, "100", "101", "99", "100.5", "1234", now_ms + 60000,
                   "123456", 42, "600", "60000", "0"]
            limit = int(request.url.params.get("limit", "1"))
            return httpx.Response(200, json=[row] * limit)
        return httpx.Response(404, json={"error": "unknown path"})

    return handler


@pytest.fixture
def binance_client() -> httpx.AsyncClient:
    transport = httpx.MockTransport(_handler_factory())
    return httpx.AsyncClient(base_url="https://api.binance.com", transport=transport)


async def test_universe_filter_applies_volume_age_status_and_leverage(
    binance_client: httpx.AsyncClient,
) -> None:
    async with BinanceMarketData(client=binance_client) as md:
        universe = await md.universe_top(limit=10)
    # Expected: BTC, ETH, SOL (in that order because of core pinning + volume).
    assert universe == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


async def test_klines_parses_rows(binance_client: httpx.AsyncClient) -> None:
    async with BinanceMarketData(client=binance_client) as md:
        candles = await md.klines("BTCUSDT", limit=3)
    assert len(candles) == 3
    assert candles[0].close == 100.5
    assert candles[0].quote_volume == 123456


async def test_exchange_info_is_cached(binance_client: httpx.AsyncClient) -> None:
    call_count = 0

    def counting_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        if request.url.path == "/api/v3/exchangeInfo":
            call_count += 1
            return httpx.Response(200, json={"symbols": []})
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(
        base_url="https://api.binance.com",
        transport=httpx.MockTransport(counting_handler),
    )
    async with BinanceMarketData(client=client) as md:
        await md.exchange_info()
        await md.exchange_info()
        await md.exchange_info()
    assert call_count == 1
