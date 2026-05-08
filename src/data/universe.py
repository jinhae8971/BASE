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


# pykrx 인덱스 코드 (KRX 공식)
INDEX_CODES: dict[str, list[str]] = {
    "KOSPI200": ["1028"],
    "KRX300": ["5300"],          # 코스피·코스닥 통합 우량주
    "KOSDAQ150": ["2203"],
    "UNION": ["1028", "2203"],   # KOSPI200 + KOSDAQ150 합집합
}


def _fetch_index_members(stock: Any, code: str, ymd: str) -> list[str]:
    """``stock.get_index_portfolio_deposit_file`` returns members for a given
    index code. Some KOSDAQ index calls require a date param — try both.
    """
    try:
        members = stock.get_index_portfolio_deposit_file(code)
        if members:
            return list(members)
    except Exception:
        pass
    try:
        return list(stock.get_index_portfolio_deposit_file(code, ymd))
    except Exception:
        return []


@lru_cache(maxsize=4)
def get_universe(as_of: date | None = None) -> list[dict[str, Any]]:
    """Return the active investable universe as a list of tickers + metadata.

    Filters by minimum market cap and 20-day ADV from ``settings.yaml``.
    Supports KOSPI200 / KRX300 / KOSDAQ150 / UNION via ``universe.index``.
    """
    as_of = as_of or date.today()
    index_name = str(get_setting("universe.index", "KOSPI200")).upper()
    min_mc = float(get_setting("universe.min_market_cap", 0))
    min_adv = float(get_setting("universe.min_adv_20d", 0))

    rows: list[dict[str, Any]] = []
    try:
        from pykrx import stock  # type: ignore

        ymd = as_of.strftime("%Y%m%d")
        codes = INDEX_CODES.get(index_name, INDEX_CODES["KOSPI200"])

        tickers: list[str] = []
        for back in range(0, 7):
            d = (as_of - timedelta(days=back)).strftime("%Y%m%d")
            collected: set[str] = set()
            for code in codes:
                collected.update(_fetch_index_members(stock, code, d))
            if collected:
                ymd = d
                tickers = sorted(collected)
                break

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

    rows = _filter_excluded(rows, as_of)

    log.info("universe.loaded", count=len(rows), index=index_name, as_of=as_of.isoformat())
    return rows


def _filter_excluded(
    rows: list[dict[str, Any]], as_of: date | None
) -> list[dict[str, Any]]:
    """Drop tickers we should never trade today.

    - 거래정지 / 관리종목 / 투자경고·주의·위험
    - User-supplied blacklist in ``settings.yaml::universe.exclude_tickers``
    - We source halt info from ``data_store/excluded_tickers.json`` if the
      operator maintains one (manual override) and from pykrx best-effort
      otherwise.
    """
    excluded: set[str] = set(get_setting("universe.exclude_tickers", []) or [])

    # Manual operator override file — easy way to exclude during incidents
    try:
        from pathlib import Path as _Path

        from common.config import get_env

        p = _Path(get_env().mais_data_dir) / "excluded_tickers.json"
        if p.exists():
            import json as _json

            data = _json.loads(p.read_text(encoding="utf-8"))
            excluded |= set(data if isinstance(data, list) else data.get("tickers", []))
    except Exception:
        pass

    # pykrx best-effort: 관리종목 + 투자경고
    try:
        from pykrx import stock  # type: ignore

        ymd = (as_of or date.today()).strftime("%Y%m%d")
        for status in ("관리종목", "투자위험", "투자경고", "투자주의"):
            try:
                tickers = stock.get_market_warning_by_ticker(ymd, status=status)
                if tickers is not None and not getattr(tickers, "empty", True):
                    excluded |= set(tickers.index.astype(str))
            except Exception:
                continue
    except Exception:
        pass

    if not excluded:
        return rows
    filtered = [r for r in rows if r["ticker"] not in excluded]
    dropped = len(rows) - len(filtered)
    if dropped > 0:
        log.info(
            "universe.excluded",
            dropped=dropped,
            sample=sorted(excluded)[:5],
        )
    return filtered


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
