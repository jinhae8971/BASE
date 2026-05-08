"""Global English RSS feeds for the MacroAgent.

Korean-only news (``data/news.py``) misses moves that originate offshore —
US Fed, ECB, China policy, oil shocks. We pull a small set of free English
feeds, rank-limit them to 20 headlines, and the MacroAgent merges these
with the Korean stream before scoring sentiment via Haiku.

No paid APIs (Bloomberg / Reuters Pro). The free Reuters world feed +
MarketWatch + Investing.com + Yahoo Finance markets cover ~95% of what
matters for KOSPI macro.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from common.logging import get_logger

log = get_logger(__name__)

# Free English RSS feeds — kept short to limit token spend
GLOBAL_FEEDS: list[tuple[str, str]] = [
    ("reuters_world", "https://feeds.reuters.com/Reuters/worldNews"),
    ("reuters_business", "https://feeds.reuters.com/reuters/businessNews"),
    ("marketwatch_top", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("yahoo_markets", "https://finance.yahoo.com/news/rssindex"),
    ("investing_econ", "https://www.investing.com/rss/news_25.rss"),  # economic indicators
    ("ft_global", "https://www.ft.com/world?format=rss"),
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


def fetch_global_macro_headlines(
    as_of: date,
    limit: int = 20,
    lookback_hours: int = 24,
) -> dict[str, Any]:
    """Pull recent English macro headlines.

    Returns ``{"as_of", "headlines": [...], "n"}``. ``headlines[i]`` is
    a dict with ``time, source, headline, link``. Sentiment scoring is
    done downstream by ``data.news._score_sentiment`` so the operator can
    re-use the same Haiku cache.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    headlines: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        import feedparser  # type: ignore
    except Exception as e:
        log.debug("global_news.feedparser_missing", error=str(e))
        return {
            "as_of": as_of.isoformat(),
            "headlines": [],
            "_stub": True,
            "_reason": "feedparser not installed",
        }

    for source, url in GLOBAL_FEEDS:
        try:
            d = feedparser.parse(url)
        except Exception as e:
            log.debug("global_news.feed_failed", source=source, error=str(e))
            continue
        for entry in d.entries[:30]:
            title = (getattr(entry, "title", "") or "").strip()
            if not title or title in seen:
                continue
            ts = _parse_dt(entry)
            if ts and ts < cutoff:
                continue
            headlines.append(
                {
                    "time": ts.isoformat() if ts else None,
                    "source": source,
                    "headline": title,
                    "link": getattr(entry, "link", ""),
                }
            )
            seen.add(title)
            if len(headlines) >= limit:
                break
        if len(headlines) >= limit:
            break

    return {"as_of": as_of.isoformat(), "headlines": headlines, "n": len(headlines)}
