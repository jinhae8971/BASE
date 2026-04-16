"""News client using yfinance built-in news feed."""
from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import yfinance as yf

from .logging_setup import get_logger
from .models import NewsItem

log = get_logger(__name__)


def fetch_news_for_ticker(ticker: str, limit: int = 5) -> list[NewsItem]:
    try:
        stock = yf.Ticker(ticker)
        raw_news = stock.news or []
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance news fetch failed for %s: %s", ticker, exc)
        return []

    items: list[NewsItem] = []
    for article in raw_news[:limit]:
        title = article.get("title", "")
        if not title:
            continue
        url = article.get("link", "") or article.get("url", "")
        publisher = article.get("publisher", None)
        pub_ts = article.get("providerPublishTime")
        published_at = None
        if pub_ts:
            with contextlib.suppress(ValueError, OSError):
                published_at = datetime.fromtimestamp(pub_ts, tz=UTC).isoformat()
        items.append(NewsItem(title=title[:200], url=url, source=publisher, published_at=published_at))
    return items
