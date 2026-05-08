"""KOSPI200 / KRX300 universe construction with caching.

Falls back to a small built-in universe when ``pykrx`` is not installed so the
pipeline still runs end-to-end (e.g. inside CI or on a fresh dev machine).
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Any

from common.config import get_setting
from common.logging import get_logger

log = get_logger(__name__)

FALLBACK_UNIVERSE: list[dict[str, Any]] = [
    {"ticker": "005930", "name": "삼성전자", "sector": "반도체"},
    {"ticker": "000660", "name": "SK하이닉스", "sector": "반도체"},
    {"ticker": "373220", "name": "LG에너지솔루션", "sector": "2차전지"},
    {"ticker": "207940", "name": "삼성바이오로직스", "sector": "바이오"},
    {"ticker": "005380", "name": "현대차", "sector": "자동차"},
    {"ticker": "000270", "name": "기아", "sector": "자동차"},
    {"ticker": "035420", "name": "NAVER", "sector": "인터넷"},
    {"ticker": "035720", "name": "카카오", "sector": "인터넷"},
    {"ticker": "051910", "name": "LG화학", "sector": "화학"},
    {"ticker": "006400", "name": "삼성SDI", "sector": "2차전지"},
    {"ticker": "068270", "name": "셀트리온", "sector": "바이오"},
    {"ticker": "105560", "name": "KB금융", "sector": "금융"},
    {"ticker": "055550", "name": "신한지주", "sector": "금융"},
    {"ticker": "012330", "name": "현대모비스", "sector": "자동차부품"},
    {"ticker": "028260", "name": "삼성물산", "sector": "지주"},
    {"ticker": "066570", "name": "LG전자", "sector": "전자"},
    {"ticker": "032830", "name": "삼성생명", "sector": "보험"},
    {"ticker": "017670", "name": "SK텔레콤", "sector": "통신"},
    {"ticker": "015760", "name": "한국전력", "sector": "유틸리티"},
    {"ticker": "034730", "name": "SK", "sector": "지주"},
]


@lru_cache(maxsize=4)
def get_universe(as_of: date | None = None) -> list[dict[str, Any]]:
    """Return the active investable universe as a list of tickers + metadata.

    Filters by minimum market cap and 20-day ADV from ``settings.yaml``.
    """
    as_of = as_of or date.today()
    index_name = str(get_setting("universe.index", "KOSPI200")).upper()
    min_mc = float(get_setting("universe.min_market_cap", 0))
    min_adv = float(get_setting("universe.min_adv_20d", 0))

    rows: list[dict[str, Any]] = []
    try:
        from pykrx import stock  # type: ignore

        ymd = as_of.strftime("%Y%m%d")
        # Walk back up to 7 calendar days to find the most recent trading day
        for back in range(0, 7):
            d = (as_of - timedelta(days=back)).strftime("%Y%m%d")
            tickers = (
                stock.get_index_portfolio_deposit_file("1028")  # KOSPI200
                if index_name == "KOSPI200"
                else stock.get_market_ticker_list(d, market="KOSPI")
            )
            if tickers:
                ymd = d
                break
        else:
            tickers = []

        cap_df = stock.get_market_cap_by_ticker(ymd)
        for tkr in tickers:
            try:
                mc = float(cap_df.loc[tkr, "시가총액"]) if tkr in cap_df.index else 0.0
                vol_avg = float(cap_df.loc[tkr, "거래대금"]) if tkr in cap_df.index else 0.0
            except Exception:
                mc, vol_avg = 0.0, 0.0
            if mc < min_mc or vol_avg < min_adv:
                continue
            rows.append(
                {
                    "ticker": tkr,
                    "name": stock.get_market_ticker_name(tkr),
                    "sector": "",  # filled by enrich_sector below
                    "market_cap": mc,
                    "adv": vol_avg,
                }
            )
        rows = _enrich_sector(rows, ymd)
    except Exception as e:
        log.warning("universe.fallback", error=str(e), reason="pykrx unavailable or offline")
        rows = list(FALLBACK_UNIVERSE)

    log.info("universe.loaded", count=len(rows), index=index_name, as_of=as_of.isoformat())
    return rows


def _enrich_sector(rows: list[dict[str, Any]], ymd: str) -> list[dict[str, Any]]:
    """Attach KOSPI200 sector index membership where possible."""
    try:
        from pykrx import stock  # type: ignore

        # KOSPI200 sub-index codes (subset).
        sector_codes = {
            "1224": "에너지·화학",
            "1225": "정보기술",
            "1226": "산업재",
            "1227": "필수소비재",
            "1228": "자유소비재",
            "1229": "헬스케어",
            "1230": "금융",
            "1231": "건설",
            "1232": "철강·소재",
            "1233": "중공업",
            "1234": "유틸리티",
            "1235": "통신서비스",
        }
        member_to_sector: dict[str, str] = {}
        for code, label in sector_codes.items():
            try:
                members = stock.get_index_portfolio_deposit_file(code)
            except Exception:
                continue
            for m in members:
                member_to_sector.setdefault(m, label)
        for r in rows:
            r["sector"] = member_to_sector.get(r["ticker"], r.get("sector") or "기타")
    except Exception:
        for r in rows:
            r.setdefault("sector", "기타")
    return rows


def sector_map(as_of: date | None = None) -> dict[str, str]:
    """Map ticker → sector label."""
    return {row["ticker"]: row.get("sector") or "기타" for row in get_universe(as_of)}


def invalidate_universe_cache() -> None:
    """Drop the cached universe so the next ``get_universe`` call re-fetches.

    Long-running schedulers must call this at end-of-day or universe stays
    frozen at boot. ``sector_map`` reads from the same cache transitively.
    """
    get_universe.cache_clear()
