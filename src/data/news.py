"""News ingestion — Korean financial RSS feeds + basic sentiment scoring.

Phase 1: fetches from 한국경제 / 연합뉴스 / 이데일리 RSS.
Sentiment is a simple keyword-based score in [-1, 1].
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

import httpx

from common.logging import get_logger

log = get_logger(__name__)

RSS_FEEDS = [
    ("한국경제",  "https://www.hankyung.com/feed/economy"),
    ("연합뉴스",  "https://www.yna.co.kr/economy/rss"),
    ("이데일리",  "https://rss.edaily.co.kr/edaily_economy.xml"),
    ("머니투데이", "https://rss.mt.co.kr/mt_money_news.xml"),
]

_POS = ["상승", "반등", "호실적", "성장", "매수", "강세", "돌파", "신고가", "개선", "흑자", "수주"]
_NEG = ["하락", "급락", "부진", "위험", "매도", "약세", "우려", "손실", "적자", "침체", "리콜"]

# Simple KOSPI200 ticker-mention patterns (company name substrings → ticker)
TICKER_HINTS: dict[str, str] = {
    "삼성전자": "005930",
    "SK하이닉스": "000660",
    "LG에너지솔루션": "373220",
    "삼성바이오": "207940",
    "현대차": "005380",
    "기아": "000270",
    "POSCO": "005490",
    "KB금융": "105560",
    "신한지주": "055550",
    "카카오": "035720",
    "네이버": "035420",
    "셀트리온": "068270",
    "LG화학": "051910",
}


def fetch_news_headlines(as_of: date | None = None, limit: int = 50) -> dict[str, Any]:
    as_of = as_of or date.today()
    per_feed = max(1, limit // len(RSS_FEEDS))
    headlines: list[dict[str, Any]] = []

    for source, url in RSS_FEEDS:
        try:
            r = httpx.get(url, timeout=10, follow_redirects=True,
                          headers={"User-Agent": "MAIS/1.0 (research bot)"})
            r.raise_for_status()
            items = _parse_rss(r.text, source)
            headlines.extend(items[:per_feed])
            log.debug("news.feed_ok", source=source, count=len(items))
        except Exception as exc:
            log.warning("news.feed_failed", source=source, error=str(exc))

    headlines = headlines[:limit]
    market_sentiment = _aggregate_sentiment(headlines)

    return {
        "as_of": as_of.isoformat(),
        "headline_count": len(headlines),
        "market_sentiment": round(market_sentiment, 3),
        "headlines": headlines,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _parse_rss(xml_text: str, source: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        desc = (item.findtext("description") or "").strip()
        text = f"{title} {desc}"

        sentiment = _score_sentiment(text)
        tickers = _extract_tickers(text)

        items.append({
            "source": source,
            "headline": title,
            "pub_date": pub_date,
            "sentiment": sentiment,
            "tickers": tickers,
        })

    return items


def _score_sentiment(text: str) -> float:
    score = 0.0
    for w in _POS:
        if w in text:
            score += 0.2
    for w in _NEG:
        if w in text:
            score -= 0.2
    return round(max(-1.0, min(1.0, score)), 2)


def _extract_tickers(text: str) -> list[str]:
    return [tkr for name, tkr in TICKER_HINTS.items() if name in text]


def _aggregate_sentiment(headlines: list[dict[str, Any]]) -> float:
    if not headlines:
        return 0.0
    return sum(h["sentiment"] for h in headlines) / len(headlines)
