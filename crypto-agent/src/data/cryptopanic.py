"""CryptoPanic free-tier news client.

Budget: 500 requests/day. Prefer the `posts` endpoint filtered by currency.
"""

from __future__ import annotations

from typing import Any


async def fetch_news(currencies: list[str], limit: int = 50) -> list[dict[str, Any]]:
    """Return news items {title, url, published_at, votes, currencies}.

    Stub returns empty list.
    """
    return []
