"""Tests for CoinGecko, DefiLlama, CryptoPanic, FRED clients."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import pytest

from src.data.cache import default_cache
from src.data.coingecko import CoinGeckoClient
from src.data.cryptopanic import BudgetExceeded, CryptoPanicClient, _budget
from src.data.defillama import DefiLlamaClient
from src.data.fred import FredClient, _parse_last_row


@pytest.fixture(autouse=True)
async def _clear() -> None:
    await default_cache().clear()


# --- CoinGecko ------------------------------------------------------------


async def test_coingecko_markets_parses_rows() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v3/coins/markets"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "bitcoin",
                    "symbol": "btc",
                    "name": "Bitcoin",
                    "market_cap": 1_200_000_000_000,
                    "fully_diluted_valuation": 1_260_000_000_000,
                    "total_volume": 50_000_000_000,
                    "price_change_percentage_24h_in_currency": 1.5,
                    "price_change_percentage_7d_in_currency": 4.2,
                }
            ],
        )

    client = httpx.AsyncClient(
        base_url="https://api.coingecko.com", transport=httpx.MockTransport(handler)
    )
    async with CoinGeckoClient(client=client) as cg:
        rows = await cg.markets(per_page=1)
    assert len(rows) == 1
    assert rows[0].symbol == "BTC"
    assert rows[0].market_cap == 1_200_000_000_000
    assert rows[0].price_change_7d_pct == 4.2


# --- DefiLlama ------------------------------------------------------------


async def test_defillama_chains_parses_rows() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v2/chains"
        return httpx.Response(
            200,
            json=[
                {"name": "Ethereum", "tvl": 50_000_000_000, "change_1d": 0.5, "change_7d": 2.1},
                {"name": "Solana", "tvl": 5_000_000_000, "change_1d": 1.2, "change_7d": 8.0},
            ],
        )

    client = httpx.AsyncClient(
        base_url="https://api.llama.fi", transport=httpx.MockTransport(handler)
    )
    async with DefiLlamaClient(client=client) as llama:
        chains = await llama.chains()
    assert [c.name for c in chains] == ["Ethereum", "Solana"]
    assert chains[0].tvl_usd == 50_000_000_000


# --- CryptoPanic ----------------------------------------------------------


async def test_cryptopanic_enforces_daily_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTOPANIC_API_KEY", "testkey")
    from src.config import get_settings
    get_settings.cache_clear()  # type: ignore[attr-defined]

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    client = httpx.AsyncClient(
        base_url="https://cryptopanic.com", transport=httpx.MockTransport(handler)
    )
    async with CryptoPanicClient(client=client) as cp:
        # Prime the budget for today, then set it at the cap so the next call
        # trips the guard. We must set `day` FIRST or the per-day reset would
        # wipe `used` back to zero inside check_and_increment.
        _budget.day = datetime.now(UTC).strftime("%Y-%m-%d")
        _budget.used = 500
        with pytest.raises(BudgetExceeded):
            await cp.posts(currencies=["BTC"])


async def test_cryptopanic_returns_empty_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRYPTOPANIC_API_KEY", raising=False)
    from src.config import get_settings
    get_settings.cache_clear()  # type: ignore[attr-defined]

    # No HTTP call should happen when the key is missing; pass a transport
    # that would fail the test if hit.
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("unexpected HTTP call without API key")

    client = httpx.AsyncClient(
        base_url="https://cryptopanic.com", transport=httpx.MockTransport(handler)
    )
    async with CryptoPanicClient(client=client) as cp:
        assert await cp.posts(currencies=["BTC"]) == []


# --- FRED -----------------------------------------------------------------


def test_fred_csv_parser_picks_last_numeric_row() -> None:
    csv = "DATE,VALUE\n2025-01-01,4.25\n2025-01-02,.\n2025-01-03,4.30\n"
    date, value = _parse_last_row(csv)
    assert date == "2025-01-03"
    assert value == 4.30


async def test_fred_latest_handles_partial_failure() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        series = req.url.params.get("id")
        if series == "DGS10":
            return httpx.Response(200, text="DATE,VALUE\n2025-01-01,4.25\n")
        # Everything else fails.
        return httpx.Response(500, text="boom")

    client = httpx.AsyncClient(
        base_url="https://fred.stlouisfed.org", transport=httpx.MockTransport(handler)
    )
    async with FredClient(client=client) as fred:
        snap = await fred.latest()
    assert snap.values.get("10Y") == 4.25
    # Other series were swallowed.
    assert set(snap.values.keys()) == {"10Y"}
