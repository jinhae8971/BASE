"""News ingestion — Korean financial RSS feeds + lightweight sentiment.

We scan a small set of public RSS feeds, dedupe, ticker-tag against the
universe, and (optionally) score sentiment via the cheap Claude Haiku model.

Sentiment scoring is *cached on disk* (keyed on headline text) so we don't
burn tokens on the same headline twice. Disabling: set
``ANTHROPIC_API_KEY=""`` or pass ``score_sentiment=False``.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from common.config import get_env
from common.logging import get_logger

from .universe import get_universe

log = get_logger(__name__)

DEFAULT_FEEDS: list[tuple[str, str]] = [
    ("yonhap_economy", "https://www.yna.co.kr/rss/economy.xml"),
    ("yonhap_market", "https://www.yna.co.kr/rss/finance.xml"),
    ("hankyung", "https://www.hankyung.com/feed/economy"),
    ("hankyung_finance", "https://www.hankyung.com/feed/finance"),
]

_SENTIMENT_PROMPT = (
    "다음 한국 금융 헤드라인에 대해 시장 영향 센티먼트를 -1.0 (매우 부정)에서 "
    "+1.0 (매우 긍정) 사이의 숫자로 평가하세요.\n"
    "JSON 배열 [{\"i\": <index>, \"s\": <score>}] 형식으로만 출력하세요.\n"
)


def _sentiment_cache_path() -> Path:
    return Path(get_env().mais_data_dir) / "news_sentiment_cache.json"


def _load_cache() -> dict[str, float]:
    p = _sentiment_cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict[str, float]) -> None:
    p = _sentiment_cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _hash(headline: str) -> str:
    return hashlib.sha1(headline.encode("utf-8")).hexdigest()[:16]


def _score_sentiment(headlines: list[str]) -> dict[str, float]:
    """Batch-score a list of headlines, using cache + a single Claude call."""
    cache = _load_cache()
    out: dict[str, float] = {}
    todo: list[tuple[int, str]] = []
    for h in headlines:
        key = _hash(h)
        if key in cache:
            out[h] = cache[key]
        else:
            todo.append((len(todo), h))
            out[h] = 0.0  # default
    if not todo:
        return out

    env = get_env()
    if not env.anthropic_api_key:
        return out

    try:
        from common.llm import call_claude

        numbered = "\n".join(f"{i}. {h}" for i, h in todo)
        raw = call_claude(
            system=_SENTIMENT_PROMPT,
            messages=[{"role": "user", "content": numbered}],
            model=env.claude_fast_model,  # Haiku — cheap
            max_tokens=1500,
            temperature=0.0,
            cache_system=True,
        )
        # Extract first JSON array
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1:
            scored = json.loads(raw[start : end + 1])
            for entry in scored:
                idx = int(entry.get("i", -1))
                score = max(-1.0, min(1.0, float(entry.get("s", 0.0))))
                if 0 <= idx < len(todo):
                    h = todo[idx][1]
                    out[h] = score
                    cache[_hash(h)] = score
        _save_cache(cache)
    except Exception as e:
        log.warning("news.sentiment_failed", error=str(e))
    return out


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
    as_of: date,
    limit: int = 80,
    lookback_hours: int = 24,
    *,
    score_sentiment: bool = True,
) -> dict[str, Any]:
    """Return deduped recent headlines tagged with universe tickers + sentiment."""
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

    if score_sentiment and headlines:
        scores = _score_sentiment([h["headline"] for h in headlines])
        for h in headlines:
            h["sentiment"] = scores.get(h["headline"], 0.0)

    overall = (
        sum(h.get("sentiment", 0.0) for h in headlines) / len(headlines)
        if headlines
        else 0.0
    )

    return {
        "as_of": as_of.isoformat(),
        "headlines": headlines,
        "n": len(headlines),
        "avg_sentiment": round(overall, 3),
    }
