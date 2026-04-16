"""Naver Finance news client — fetch recent headlines for a stock ticker."""
from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from .logging_setup import get_logger
from .models import NewsItem

log = get_logger(__name__)

NAVER_NEWS_URL = "https://finance.naver.com/item/news_news.naver"


def fetch_news_for_ticker(ticker: str, limit: int = 5) -> list[NewsItem]:
    """Scrape recent Naver Finance news headlines for a given stock ticker.

    Returns an empty list on any failure — the pipeline should still work
    without news context.
    """
    params = {
        "code": ticker,
        "page": "1",
        "sm": "title_entity_id.basic",
        "clusterId": "",
    }
    headers = {
        "user-agent": "Mozilla/5.0 (compatible; kospi-research-agent/0.1)",
        "referer": f"https://finance.naver.com/item/main.naver?code={ticker}",
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(NAVER_NEWS_URL, params=params, headers=headers)
            r.raise_for_status()
            html = r.text
    except Exception as exc:  # noqa: BLE001
        log.warning("naver news fetch failed for %s: %s", ticker, exc)
        return []

    return _parse_news_html(html, limit)


def _parse_news_html(html: str, limit: int) -> list[NewsItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[NewsItem] = []

    # Naver Finance news table rows (try multiple selectors for resilience)
    rows = soup.select("table.type5 tr") or soup.select(".news_list tr") or soup.select("table tr")
    if not rows:
        log.warning("naver news HTML has no matching table rows — page structure may have changed")
    for row in rows:
        if len(items) >= limit:
            break
        title_tag = row.select_one("td.title a")
        date_tag = row.select_one("td.date")
        source_tag = row.select_one("td.info")

        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        if not title:
            continue

        href = title_tag.get("href", "")
        # Make URL absolute if relative
        if href.startswith("/"):
            url = f"https://finance.naver.com{href}"
        elif href.startswith("http"):
            url = href
        else:
            url = ""

        source = source_tag.get_text(strip=True) if source_tag else None
        published = date_tag.get_text(strip=True) if date_tag else None

        items.append(
            NewsItem(
                title=_clean_text(title)[:200],
                url=url,
                source=source,
                published_at=published,
            )
        )

    return items


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
