"""Binance public market data client.

Public REST endpoints only -- no key required for the read-only data the
agents need. Paper/live trading will use a separate authenticated client in
`execution.binance_client`.

Endpoints used:
  - /api/v3/exchangeInfo           : symbol metadata, filters, listing time
  - /api/v3/ticker/24hr            : 24h rolling stats per symbol
  - /api/v3/klines                 : OHLCV candles

Universe filter (configurable from Settings):
  - Quote currency == USDT
  - Spot + TRADING status
  - 24h quote volume >= min_24h_volume_usd
  - Listed at least min_listing_age_days
  - Exclude leveraged tokens (UP/DOWN/BULL/BEAR suffixes)
  - Exclude stablecoin quote pairs (USDC, TUSD, FDUSD, BUSD, DAI, ...)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.config import get_settings
from src.data.archive import append as archive_append
from src.data.cache import cached, default_cache
from src.data.http import build_client, configure_rate_limit, get_json
from src.logging import get_logger

log = get_logger("binance_md")

BASE_URL = "https://api.binance.com"
HOST = "api.binance.com"

# Binance public IP limit is 6000 req-weight/min. We stay well under by
# capping at 60 req/10s for our light usage.
configure_rate_limit(HOST, requests=60, per_seconds=10.0)

STABLE_QUOTES = {"USDC", "TUSD", "FDUSD", "BUSD", "DAI", "USDP", "PYUSD"}
LEVERAGED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")


@dataclass
class Candle:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float


@dataclass
class SymbolInfo:
    symbol: str
    base: str
    status: str
    onboard_date: datetime | None


@dataclass
class Ticker24h:
    symbol: str
    last_price: float
    price_change_pct: float
    quote_volume: float  # 24h quote volume in USDT


class BinanceMarketData:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or build_client(base_url=BASE_URL)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> BinanceMarketData:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # --- raw endpoints ------------------------------------------------

    async def exchange_info(self) -> dict[str, Any]:
        async def _load() -> dict[str, Any]:
            data = await get_json(self._client, "/api/v3/exchangeInfo", host=HOST)
            archive_append("binance_exchange_info", {"symbols_count": len(data.get("symbols", []))})
            return data

        return await cached("binance:exchangeInfo", ttl=3600, loader=_load, cache=default_cache())

    async def ticker_24h(self) -> list[dict[str, Any]]:
        async def _load() -> list[dict[str, Any]]:
            data = await get_json(self._client, "/api/v3/ticker/24hr", host=HOST)
            archive_append("binance_ticker_24h", {"count": len(data)})
            return data

        return await cached("binance:ticker24h", ttl=60, loader=_load, cache=default_cache())

    async def klines(
        self,
        symbol: str,
        interval: str = "1d",
        limit: int = 200,
    ) -> list[Candle]:
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        raw = await get_json(self._client, "/api/v3/klines", params=params, host=HOST)
        return [_parse_kline(row) for row in raw]

    # --- derived -------------------------------------------------------

    async def symbols(self) -> list[SymbolInfo]:
        info = await self.exchange_info()
        out: list[SymbolInfo] = []
        for s in info.get("symbols", []):
            onboard_ms = s.get("onboardDate") or 0
            onboard = (
                datetime.fromtimestamp(onboard_ms / 1000, tz=UTC) if onboard_ms else None
            )
            out.append(
                SymbolInfo(
                    symbol=s["symbol"],
                    base=s["baseAsset"],
                    status=s.get("status", "UNKNOWN"),
                    onboard_date=onboard,
                )
            )
        return out

    async def tickers(self) -> list[Ticker24h]:
        rows = await self.ticker_24h()
        return [
            Ticker24h(
                symbol=r["symbol"],
                last_price=float(r.get("lastPrice", 0.0) or 0.0),
                price_change_pct=float(r.get("priceChangePercent", 0.0) or 0.0),
                quote_volume=float(r.get("quoteVolume", 0.0) or 0.0),
            )
            for r in rows
        ]

    async def universe_top(self, limit: int | None = None) -> list[str]:
        """Return the top USDT spot pairs that pass the universe filter.

        Ordering: by 24h quote volume descending. `limit` defaults to
        `Settings.universe_size`. Core assets (BTC, ETH) are always included
        at the top of the list regardless of volume ranking.
        """
        s = get_settings()
        size = limit or s.universe_size

        syms = {si.symbol: si for si in await self.symbols()}
        tickers = await self.tickers()

        now = datetime.now(UTC)
        min_age = timedelta(days=s.min_listing_age_days)

        eligible: list[Ticker24h] = []
        for t in tickers:
            sym = t.symbol
            if not sym.endswith("USDT"):
                continue
            if sym.endswith(LEVERAGED_SUFFIXES):
                continue
            info = syms.get(sym)
            if info is None or info.status != "TRADING":
                continue
            # Skip stablecoin-on-stablecoin and meta pairs.
            if info.base in STABLE_QUOTES or info.base == "USDT":
                continue
            if t.quote_volume < s.min_24h_volume_usd:
                continue
            if info.onboard_date and (now - info.onboard_date) < min_age:
                continue
            eligible.append(t)

        eligible.sort(key=lambda x: x.quote_volume, reverse=True)
        ranked = [t.symbol for t in eligible]

        # Enforce core assets at the head.
        out: list[str] = []
        for core in s.core_assets:
            if core in ranked:
                out.append(core)
                ranked.remove(core)
        out.extend(ranked)
        return out[:size]


def _parse_kline(row: list[Any]) -> Candle:
    return Candle(
        open_time=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
        quote_volume=float(row[7]),
    )


# Back-compat thin functions used by Phase 0 orchestrator. They keep the
# older signature but now hit real Binance when not in a test.
async def fetch_universe_top(limit: int = 20) -> list[str]:
    async with BinanceMarketData() as md:
        return await md.universe_top(limit=limit)


async def fetch_klines(symbol: str, interval: str = "1d", limit: int = 200) -> list[Candle]:
    async with BinanceMarketData() as md:
        return await md.klines(symbol, interval=interval, limit=limit)
