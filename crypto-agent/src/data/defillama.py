"""DefiLlama client: TVL by chain and protocol. No key, unlimited."""

from __future__ import annotations

from typing import Any


async def fetch_chain_tvl() -> dict[str, float]:
    """Chain -> USD TVL. Stub returns empty dict."""
    return {}


async def fetch_protocol_tvl(slug: str) -> dict[str, Any]:
    """Per-protocol TVL timeseries. Stub returns empty dict."""
    return {}
