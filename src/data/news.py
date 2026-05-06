"""News ingestion — Korean financial RSS feeds.

We scan a small set of public RSS feeds and return a deduped list of recent
headlines. No paid API. Ticker tagging is a simple keyword match against the
universe — good enough for prompt context.

If ``feedparser`` is not installed we degrade silently so the rest of the
pipeline keeps running.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from common.logging import get_logger

from .universe import get_universe

log = get_logger(__name__)

DEFAULT_FEEDS: list[tuple[str, str]] = [
    ("yonhap_economy", "https://www.yna.co.kr/rss/economy.xml"),
    ("yonhap_market", "https://www.yna.co.kr/rss/finance.xml"),
    ("hankyung", "https://www.hankyung.com/feed/economy"),
    ("hankyung_finance", "https://www.hankyung.com/feed/finance"),
]


def _parse_dt(entry: Any) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        v = getattr(entry, attr, None)
        if v:
            try:
                return datetime(*v[:6], tzinfo=UTC)
            except Exception:
                continue
    return None


def fetch_news_headlines(
    as_of: date, limit: int = 80, lookback_hours: int = 24
) -> dict[str, Any]:
    """Return deduped recent headlines tagged with universe tickers."""
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)

    headlines: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        import feedparser  # type: ignore
    except Exception as e:
        log.debug("news.feedparser_unavailable", error=str(e))
        return {
            "as_of": as_of.isoformat(),
            "headlines": [],
            "_stub": True,
            "_reason": "feedparser not installed",
        }

    name_lookup = {r["ticker"]: r.get("name", "") for r in get_universe(as_of)}

    for source, url in DEFAULT_FEEDS:
        try:
            d = feedparser.parse(url)
        except Exception as e:
            log.debug("news.feed_failed", source=source, error=str(e))
            continue
        for entry in d.entries[:50]:
            title = (getattr(entry, "title", "") or "").strip()
            if not title or title in seen:
                continue
            ts = _parse_dt(entry)
            if ts and ts < cutoff:
                continue
            tagged = [
                tkr
                for tkr, nm in name_lookup.items()
                if nm and len(nm) >= 2 and nm in title
            ]
            headlines.append(
                {
                    "time": ts.isoformat() if ts else None,
                    "source": source,
                    "headline": title,
                    "link": getattr(entry, "link", ""),
                    "tickers": tagged,
                }
            )
            seen.add(title)
            if len(headlines) >= limit:
                break
        if len(headlines) >= limit:
            break

    return {"as_of": as_of.isoformat(), "headlines": headlines, "n": len(headlines)}
