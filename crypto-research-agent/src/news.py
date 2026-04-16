"""CryptoPanic news client (free tier)."""
from __future__ import annotations

import httpx

from .config import get_settings
from .logging_setup import get_logger
from .models import NewsItem

log = get_logger(__name__)

CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"


def fetch_news_for_symbol(symbol: str, limit: int = 5) -> list[NewsItem]:
    """Fetch recent news posts filtered by currency symbol.

    Returns an empty list if no API key is configured or on any failure —
    the pipeline should still work without news.
    """
    settings = get_settings()
    if not settings.cryptopanic_key:
        return []

    params = {
        "auth_token": settings.cryptopanic_key,
        "currencies": symbol.upper(),
        "public": "true",
        "kind": "news",
        "filter": "hot",
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(CRYPTOPANIC_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("cryptopanic fetch failed for %s: %s", symbol, exc)
        return []

    items: list[NewsItem] = []
    for post in (data.get("results") or [])[:limit]:
        items.append(
            NewsItem(
                title=post.get("title", "")[:200],
                url=post.get("url", ""),
                source=(post.get("source") or {}).get("title"),
                published_at=post.get("published_at"),
            )
        )
    return items
