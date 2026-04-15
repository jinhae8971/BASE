"""Binance market data client (public endpoints, no key required).

Phase 1 will add: OHLCV fetch, orderbook snapshot, 24h stats, symbol filters.
Dry mode returns deterministic synthetic data so the orchestrator can run
offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class Candle:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


async def fetch_universe_top(limit: int = 50) -> list[str]:
    """Top `limit` Binance USDT spot pairs by 24h quote volume.

    Stub: returns a fixed synthetic list so tests are deterministic.
    """
    base = [
        "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOGE", "LINK", "DOT",
        "MATIC", "TRX", "LTC", "ATOM", "UNI", "NEAR", "APT", "ARB", "OP", "INJ",
    ]
    return [f"{c}USDT" for c in base[:limit]]


async def fetch_klines(symbol: str, interval: str = "1d", limit: int = 200) -> list[Candle]:
    """Return last `limit` candles for `symbol`.

    Stub: synthetic flat series anchored at the current time.
    """
    now = datetime.now(UTC)
    return [
        Candle(open_time=now, open=100.0, high=101.0, low=99.0, close=100.0, volume=1e6)
        for _ in range(limit)
    ]
