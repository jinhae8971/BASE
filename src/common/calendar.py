"""KRX trading-day calendar.

Korean exchange is closed on:
    - Public holidays (New Year, 설, 추석, ...)
    - Election days
    - Year-end (December 31, when it falls on a weekday)
    - Ad-hoc 임시휴장 (rare, but happens)

We use ``pykrx.get_business_days`` when available — it consults the actual
KRX calendar and is the authoritative source. The result is cached on disk
in ``$MAIS_DATA_DIR/krx_business_days.json`` so the scheduler doesn't re-hit
KRX every time.

Fallback when pykrx is unreachable: assume mon-fri (the vast majority of
days are trading days; we'll accept the rare false-positive over a hard
crash).
"""
from __future__ import annotations

import contextlib
import json
from datetime import date, timedelta
from pathlib import Path

from common.config import get_env
from common.logging import get_logger

log = get_logger(__name__)


def _cache_path() -> Path:
    return Path(get_env().mais_data_dir) / "krx_business_days.json"


def _load_cache() -> dict[str, list[str]]:
    p = _cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(data: dict[str, list[str]]) -> None:
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def trading_days(year: int) -> set[date]:
    """All KRX business days for a given year."""
    cache = _load_cache()
    key = str(year)
    if key in cache:
        return {date.fromisoformat(d) for d in cache[key]}

    days: set[date] = set()
    try:
        from pykrx import stock  # type: ignore

        start = f"{year}0101"
        end = f"{year}1231"
        # pykrx.stock.get_previous_business_day / get_nearest_business_day exist;
        # the simplest reliable iterator is via market OHLCV index.
        df = stock.get_index_ohlcv_by_date(start, end, "1001")
        if df is not None and not df.empty:
            for ts in df.index:
                days.add(ts.date() if hasattr(ts, "date") else ts)
    except Exception as e:
        log.debug("calendar.pykrx_failed", year=year, error=str(e))

    if days:
        cache[key] = sorted(d.isoformat() for d in days)
        _save_cache(cache)
        return days

    # Fallback: weekdays only (mon-fri).
    log.warning("calendar.fallback_weekdays", year=year)
    out: set[date] = set()
    d = date(year, 1, 1)
    while d.year == year:
        if d.weekday() < 5:
            out.add(d)
        d += timedelta(days=1)
    return out


def is_trading_day(when: date) -> bool:
    return when in trading_days(when.year)


def next_trading_day(after: date) -> date:
    """First trading day strictly after ``after``."""
    days = trading_days(after.year)
    candidate = after + timedelta(days=1)
    # Walk forward at most 14 days. If we cross year boundary, expand.
    for _ in range(20):
        if candidate.year != after.year:
            days |= trading_days(candidate.year)
        if candidate in days:
            return candidate
        candidate += timedelta(days=1)
    return candidate  # give up — caller can decide what to do
