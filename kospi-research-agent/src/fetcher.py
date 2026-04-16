"""KRX market data client using pykrx."""
from __future__ import annotations

from datetime import datetime, timedelta

from pykrx import stock as krx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import get_settings
from .logging_setup import get_logger
from .models import StockMarket

log = get_logger(__name__)

# ETF / ETN / 리츠 등 비순수주식 종목 이름 패턴
_EXCLUDE_NAME_PATTERNS = (
    "ETF", "ETN", "인버스", "레버리지", "KODEX", "TIGER", "KBSTAR",
    "ARIRANG", "HANARO", "SOL ", "ACE ", "KOSEF", "KINDEX",
    "FOCUS", "파워", "리츠", "스팩", "SPAC",
)

# 우선주 접미사: 종목코드 끝자리 0이 아닌 보통주 파생
_PREFERRED_SUFFIXES = {"5", "7", "8", "9"}


def _is_excluded(ticker: str, name: str) -> bool:
    """Exclude ETFs, ETNs, SPACs, REITs, preferred shares."""
    if name[-1:] == "우" or "우B" in name or "우(전환)" in name:
        return True
    if ticker[-1] in _PREFERRED_SUFFIXES:
        return True
    upper = name.upper()
    return any(pat in upper for pat in _EXCLUDE_NAME_PATTERNS)


def _recent_trading_date() -> str:
    """Get most recent trading date in YYYYMMDD format."""
    today = datetime.now()
    # pykrx에서 최근 거래일 조회 (주말/공휴일 자동 처리)
    for delta in range(7):
        dt = today - timedelta(days=delta)
        date_str = dt.strftime("%Y%m%d")
        tickers = krx.get_market_ohlcv_by_ticker(date_str, market="KOSPI")
        if not tickers.empty:
            return date_str
    raise RuntimeError("no trading data found within the last 7 days")


def _past_trading_date(days_back: int) -> str:
    """Get the trading date N trading days before the most recent one."""
    today = datetime.now()
    trading_dates_found = 0
    for delta in range(1, 30):
        dt = today - timedelta(days=delta)
        date_str = dt.strftime("%Y%m%d")
        tickers = krx.get_market_ohlcv_by_ticker(date_str, market="KOSPI")
        if not tickers.empty:
            trading_dates_found += 1
            if trading_dates_found >= days_back:
                return date_str
    raise RuntimeError(f"could not find {days_back} past trading days")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def fetch_market(date_str: str, market: str) -> list[StockMarket]:
    """Fetch OHLCV + market cap for all stocks on a given trading date."""
    log.info("fetching %s data for %s", market, date_str)

    ohlcv = krx.get_market_ohlcv_by_ticker(date_str, market=market)
    cap = krx.get_market_cap_by_ticker(date_str, market=market)

    if ohlcv.empty:
        log.warning("no OHLCV data for %s %s", market, date_str)
        return []

    expected_cols = {"종가", "시가", "고가", "저가", "거래량", "거래대금", "등락률"}
    missing_cols = expected_cols - set(ohlcv.columns)
    if missing_cols:
        log.warning("pykrx columns missing: %s (available: %s)", missing_cols, list(ohlcv.columns))

    stocks: list[StockMarket] = []
    for ticker in ohlcv.index:
        name = krx.get_market_ticker_name(ticker)
        if _is_excluded(ticker, name):
            continue

        row = ohlcv.loc[ticker]
        close = float(row.get("종가", 0))
        if close <= 0:
            continue

        mcap = 0.0
        if cap is not None and ticker in cap.index:
            mcap = float(cap.loc[ticker].get("시가총액", 0))

        stocks.append(
            StockMarket(
                ticker=ticker,
                name=name,
                market=market,
                close=close,
                open=float(row.get("시가", 0)),
                high=float(row.get("고가", 0)),
                low=float(row.get("저가", 0)),
                volume=int(row.get("거래량", 0)),
                trading_value=float(row.get("거래대금", 0)),
                market_cap=mcap,
                change_pct=float(row.get("등락률", 0.0)),
            )
        )

    log.info("fetched %d stocks for %s on %s", len(stocks), market, date_str)
    return stocks


def fetch_all_markets() -> tuple[list[StockMarket], str]:
    """Fetch stocks for configured market(s). Returns (stocks, trading_date)."""
    settings = get_settings()
    date_str = _recent_trading_date()

    if settings.market == "ALL":
        stocks = fetch_market(date_str, "KOSPI") + fetch_market(date_str, "KOSDAQ")
    else:
        stocks = fetch_market(date_str, settings.market)

    return stocks, date_str


def get_past_trading_date(days_back: int) -> str:
    """Public wrapper for _past_trading_date."""
    return _past_trading_date(days_back)


def get_recent_trading_date() -> str:
    """Public wrapper for _recent_trading_date."""
    return _recent_trading_date()
