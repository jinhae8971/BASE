"""CoinGecko free-tier client: categories, market cap, FDV.

Free tier rate limit: ~30 req/min. Cache all responses in Redis for 5 min.
"""

from __future__ import annotations

from typing import Any


async def fetch_categories() -> list[dict[str, Any]]:
    """Sector classification. Stub returns empty list."""
    return []


async def fetch_market_data(ids: list[str]) -> dict[str, dict[str, Any]]:
    """Per-coin market cap, FDV, volume. Stub returns zeros."""
    return {cid: {"market_cap": 0.0, "fdv": 0.0, "volume_24h": 0.0} for cid in ids}
