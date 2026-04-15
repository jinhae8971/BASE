"""MarketSnapshot -- single input bundle for a daily run.

The orchestrator calls `gather()` exactly once at the top of a run, then
hands the resulting `MarketSnapshot` to every agent via `AgentContext`. This
centralizes rate limits, caching, and archival so individual agents don't
each re-fetch the same data.

Fan-out runs the five sources concurrently via `asyncio.gather` with
`return_exceptions=True`: one flaky source (e.g. FRED rate-limited) does not
fail the whole run -- the affected fields are simply left empty and the agents
degrade gracefully.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.data.binance_md import BinanceMarketData, Candle
from src.data.coingecko import Category, CoinGeckoClient, MarketRow
from src.data.cryptopanic import CryptoPanicClient, NewsItem
from src.data.defillama import ChainTvl, DefiLlamaClient
from src.data.fred import FredClient, MacroSnapshot
from src.logging import get_logger

log = get_logger("snapshot")


@dataclass
class MarketSnapshot:
    as_of: datetime
    universe: list[str] = field(default_factory=list)
    # Binance
    candles: dict[str, list[Candle]] = field(default_factory=dict)
    # CoinGecko
    markets: list[MarketRow] = field(default_factory=list)
    categories: list[Category] = field(default_factory=list)
    # DefiLlama
    chain_tvl: list[ChainTvl] = field(default_factory=list)
    # CryptoPanic
    news: list[NewsItem] = field(default_factory=list)
    # FRED
    macro: MacroSnapshot = field(
        default_factory=lambda: MacroSnapshot(values={}, as_of={})
    )
    errors: dict[str, str] = field(default_factory=dict)

    def agent_dict(self) -> dict[str, Any]:
        """Flatten to the dict shapes the agents expect."""
        return {
            "market_data": {
                "candles": {
                    sym: [
                        {
                            "t": c.open_time.isoformat(),
                            "o": c.open,
                            "h": c.high,
                            "l": c.low,
                            "c": c.close,
                            "v": c.volume,
                            "qv": c.quote_volume,
                        }
                        for c in candles
                    ]
                    for sym, candles in self.candles.items()
                },
                "markets": [
                    {
                        "symbol": m.symbol,
                        "market_cap": m.market_cap,
                        "fdv": m.fdv,
                        "volume_24h": m.volume_24h,
                        "chg24h": m.price_change_24h_pct,
                        "chg7d": m.price_change_7d_pct,
                    }
                    for m in self.markets
                ],
                "categories": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "market_cap": c.market_cap,
                        "volume_24h": c.volume_24h,
                    }
                    for c in self.categories
                ],
            },
            "macro_data": {
                "fred": self.macro.values,
                "as_of": self.macro.as_of,
            },
            "onchain_data": {
                "chain_tvl": [
                    {
                        "name": c.name,
                        "tvl": c.tvl_usd,
                        "chg1d": c.change_1d_pct,
                        "chg7d": c.change_7d_pct,
                    }
                    for c in self.chain_tvl
                ],
            },
            "news": [
                {
                    "title": n.title,
                    "url": n.url,
                    "published_at": n.published_at.isoformat(),
                    "currencies": n.currencies,
                    "votes": n.votes,
                }
                for n in self.news
            ],
        }


async def gather(
    universe_size: int = 20,
    klines_per_symbol: int = 120,
    news_limit: int = 50,
) -> MarketSnapshot:
    """Collect one full snapshot. Never raises; errors land in snapshot.errors."""
    snap = MarketSnapshot(as_of=datetime.now(UTC))

    async with (
        BinanceMarketData() as binance,
        CoinGeckoClient() as cg,
        DefiLlamaClient() as llama,
        CryptoPanicClient() as cp,
        FredClient() as fred,
    ):
        # 1. Universe first -- many downstream calls depend on it.
        try:
            snap.universe = await binance.universe_top(limit=universe_size)
        except Exception as exc:  # noqa: BLE001
            snap.errors["binance.universe"] = str(exc)
            log.error("snapshot.universe_failed", err=str(exc))
            return snap

        # 2. Everything else concurrently.
        klines_task = _gather_klines(binance, snap.universe, klines_per_symbol)
        markets_task = cg.markets(per_page=100)
        categories_task = cg.categories()
        chains_task = llama.chains()
        bases = [sym[: -len("USDT")] for sym in snap.universe[:10]]
        news_task = cp.posts(currencies=bases)
        macro_task = fred.latest()

        results = await asyncio.gather(
            klines_task,
            markets_task,
            categories_task,
            chains_task,
            news_task,
            macro_task,
            return_exceptions=True,
        )
        keys = ("binance.klines", "coingecko.markets", "coingecko.categories",
                "defillama.chains", "cryptopanic.news", "fred.latest")
        for key, res in zip(keys, results, strict=True):
            if isinstance(res, Exception):
                snap.errors[key] = str(res)
                log.warning("snapshot.partial_failure", source=key, err=str(res))
                continue
            if key == "binance.klines":
                snap.candles = res  # type: ignore[assignment]
            elif key == "coingecko.markets":
                snap.markets = res  # type: ignore[assignment]
            elif key == "coingecko.categories":
                snap.categories = res  # type: ignore[assignment]
            elif key == "defillama.chains":
                snap.chain_tvl = res  # type: ignore[assignment]
            elif key == "cryptopanic.news":
                snap.news = res  # type: ignore[assignment]
            elif key == "fred.latest":
                snap.macro = res  # type: ignore[assignment]

    log.info(
        "snapshot.done",
        universe=len(snap.universe),
        candles=len(snap.candles),
        markets=len(snap.markets),
        chains=len(snap.chain_tvl),
        news=len(snap.news),
        errors=list(snap.errors),
    )
    return snap


async def _gather_klines(
    binance: BinanceMarketData,
    universe: list[str],
    limit: int,
) -> dict[str, list[Candle]]:
    async def _one(sym: str) -> tuple[str, list[Candle]]:
        try:
            return sym, await binance.klines(sym, interval="1d", limit=limit)
        except Exception as exc:  # noqa: BLE001
            log.warning("snapshot.kline_failed", symbol=sym, err=str(exc))
            return sym, []

    pairs = await asyncio.gather(*(_one(s) for s in universe))
    return {s: cs for s, cs in pairs if cs}
