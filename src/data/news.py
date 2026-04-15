"""News ingestion — RSS feeds + 네이버금융 crawling.

TODO(Phase 1): wire to 한경/연합/네이버금융 RSS + local sentiment classifier.
"""
from __future__ import annotations

from datetime import date
from typing import Any


def fetch_news_headlines(as_of: date, limit: int = 50) -> dict[str, Any]:
    return {
        "as_of": as_of.isoformat(),
        "headlines": [],  # list of {time, source, headline, tickers, sentiment}
        "_stub": True,
    }
