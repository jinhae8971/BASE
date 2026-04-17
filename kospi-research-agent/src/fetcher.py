"""KRX market data client using direct KRX Open API via httpx (no pykrx)."""
from __future__ import annotations

from datetime import datetime, timedelta

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import get_settings
from .logging_setup import get_logger
from .models import StockMarket

log = get_logger(__name__)

KRX_OHLCV_URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
_EXCLUDE_NAME_PATTERNS = (
    "ETF", "ETN", "인버스", "레버리지", "KODEX", "TIGER", "KBSTAR",
    "ARIRANG", "HANARO", "SOL ", "ACE ", "KOSEF", "KINDEX",
    "FOCUS", "파워", "리츠", "스팩", "SPAC",
)
_PREFERRED_SUFFIXES = {"5", "7", "8", "9"}

_MARKET_ID = {"KOSPI": "STK", "KOSDAQ": "KSQ"}


def _is_excluded(ticker: str, name: str) -> bool:
    if name[-1:] == "우" or "우B" in name or "우(전환)" in name:
        return True
    if ticker[-1] in _PREFERRED_SUFFIXES:
        return True
    upper = name.upper()
    return any(pat in upper for pat in _EXCLUDE_NAME_PATTERNS)


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=16))
def _fetch_krx_ohlcv(date_str: str, market: str) -> list[dict]:
    """Fetch OHLCV for all stocks on a date directly from KRX."""
    mkt_id = _MARKET_ID.get(market, "STK")
    payload = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
        "locale": "ko_KR",
        "mktId": mkt_id,
        "trdDd": date_str,
        "share": "1",
        "money": "1",
        "csvxls_is498": "false",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiStat/stats/MDCSTAT01501.cmd",
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(KRX_OHLCV_URL, data=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    return data.get("OutBlock_1") or []


def _recent_trading_date() -> str:
    today = datetime.now()
    for delta in range(7):
        dt = today - timedelta(days=delta)
        date_str = dt.strftime("%Y%m%d")
        rows = _fetch_krx_ohlcv(date_str, "KOSPI")
        if rows:
            return date_str
    raise RuntimeError("no trading data found within the last 7 days")


def _past_trading_date(days_back: int) -> str:
    today = datetime.now()
    trading_dates_found = 0
    for delta in range(1, 30):
        dt = today - timedelta(days=delta)
        date_str = dt.strftime("%Y%m%d")
        rows = _fetch_krx_ohlcv(date_str, "KOSPI")
        if rows:
            trading_dates_found += 1
            if trading_dates_found >= days_back:
                return date_str
    raise RuntimeError(f"could not find {days_back} past trading days")


def _parse_int(val: str | int | float) -> int:
    if isinstance(val, (int, float)):
        return int(val)
    return int(str(val).replace(",", "") or "0")


def _parse_float(val: str | int | float) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    return float(str(val).replace(",", "") or "0")


def fetch_market(date_str: str, market: str) -> list[StockMarket]:
    log.info("fetching %s data for %s", market, date_str)
    rows = _fetch_krx_ohlcv(date_str, market)
    if not rows:
        log.warning("no data for %s %s", market, date_str)
        return []

    stocks: list[StockMarket] = []
    for row in rows:
        ticker = row.get("ISU_SRT_CD", "")
        name = row.get("ISU_ABBRV", "")
        if not ticker or not name:
            continue
        if _is_excluded(ticker, name):
            continue

        close = _parse_float(row.get("TDD_CLSPRC", 0))
        if close <= 0:
            continue

        fluc_rt = _parse_float(row.get("FLUC_RT", 0))
        trading_val = _parse_float(row.get("ACC_TRDVAL", 0))
        mkt_cap = _parse_float(row.get("MKTCAP", 0))

        stocks.append(
            StockMarket(
                ticker=ticker,
                name=name,
                market=market,
                close=close,
                open=_parse_float(row.get("TDD_OPNPRC", 0)),
                high=_parse_float(row.get("TDD_HGPRC", 0)),
                low=_parse_float(row.get("TDD_LWPRC", 0)),
                volume=_parse_int(row.get("ACC_TRDVOL", 0)),
                trading_value=trading_val,
                market_cap=mkt_cap,
                change_pct=fluc_rt,
            )
        )

    log.info("fetched %d stocks for %s on %s", len(stocks), market, date_str)
    return stocks


def fetch_all_markets() -> tuple[list[StockMarket], str]:
    settings = get_settings()
    date_str = _recent_trading_date()
    if settings.market == "ALL":
        stocks = fetch_market(date_str, "KOSPI") + fetch_market(date_str, "KOSDAQ")
    else:
        stocks = fetch_market(date_str, settings.market)
    return stocks, date_str


def get_past_trading_date(days_back: int) -> str:
    return _past_trading_date(days_back)


def get_recent_trading_date() -> str:
    return _recent_trading_date()
