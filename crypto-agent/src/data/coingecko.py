"""CoinGecko free-tier client.

Free tier: ~30 req/min, no key required for the public demo endpoints we use.
We cache aggressively in the in-process TTL cache (5 min) and archive the raw
responses under `data/archive/coingecko_*/`.

Endpoints:
  - /api/v3/coins/markets        : market cap, FDV, volume, 24h change
  - /api/v3/coins/categories     : sector classification
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from src.data.archive import append as archive_append
from src.data.cache import cached, default_cache
from src.data.http import build_client, configure_rate_limit, get_json
from src.logging import get_logger

log = get_logger("coingecko")

BASE_URL = "https://api.coingecko.com"
HOST = "api.coingecko.com"

# 30 req/min free tier. Stay under with 25/min for safety.
configure_rate_limit(HOST, requests=25, per_seconds=60.0)


@dataclass
class MarketRow:
    id: str
    symbol: str
    name: str
    market_cap: float
    fdv: float
    volume_24h: float
    price_change_24h_pct: float
    price_change_7d_pct: float


@dataclass
class Category:
    id: str
    name: str
    market_cap: float
    market_cap_change_24h: float
    volume_24h: float


class CoinGeckoClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or build_client(base_url=BASE_URL)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> CoinGeckoClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def markets(self, per_page: int = 100, page: int = 1) -> list[MarketRow]:
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": page,
            "sparkline": "false",
            "price_change_percentage": "24h,7d",
        }
        key = f"cg:markets:{per_page}:{page}"

        async def _load() -> list[dict[str, Any]]:
            raw = await get_json(
                self._client, "/api/v3/coins/markets", params=params, host=HOST
            )
            archive_append("coingecko_markets", {"count": len(raw), "page": page})
            return raw

        rows = await cached(key, ttl=300, loader=_load, cache=default_cache())
        return [
            MarketRow(
                id=r["id"],
                symbol=r["symbol"].upper(),
                name=r["name"],
                market_cap=float(r.get("market_cap") or 0.0),
                fdv=float(r.get("fully_diluted_valuation") or 0.0),
                volume_24h=float(r.get("total_volume") or 0.0),
                price_change_24h_pct=float(r.get("price_change_percentage_24h_in_currency") or 0.0),
                price_change_7d_pct=float(r.get("price_change_percentage_7d_in_currency") or 0.0),
            )
            for r in rows
        ]

    async def categories(self) -> list[Category]:
        async def _load() -> list[dict[str, Any]]:
            raw = await get_json(self._client, "/api/v3/coins/categories", host=HOST)
            archive_append("coingecko_categories", {"count": len(raw)})
            return raw

        rows = await cached("cg:categories", ttl=600, loader=_load, cache=default_cache())
        return [
            Category(
                id=r.get("id", ""),
                name=r.get("name", ""),
                market_cap=float(r.get("market_cap") or 0.0),
                market_cap_change_24h=float(r.get("market_cap_change_24h") or 0.0),
                volume_24h=float(r.get("volume_24h") or 0.0),
            )
            for r in rows
        ]
