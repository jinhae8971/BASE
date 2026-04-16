"""S&P 500 market data client using yfinance + Wikipedia for constituents."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from .logging_setup import get_logger
from .models import StockMarket

log = get_logger(__name__)

SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _fetch_sp500_constituents() -> pd.DataFrame:
    """Scrape current S&P 500 list from Wikipedia."""
    tables = pd.read_html(SP500_WIKI_URL)
    df = tables[0]
    # Columns: Symbol, Security, GICS Sector, GICS Sub-Industry, ...
    df = df.rename(columns={
        "Symbol": "ticker",
        "Security": "name",
        "GICS Sector": "sector",
        "GICS Sub-Industry": "sub_industry",
    })
    # Fix tickers with dots (BRK.B → BRK-B for yfinance)
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    log.info("fetched %d S&P 500 constituents from Wikipedia", len(df))
    return df[["ticker", "name", "sector", "sub_industry"]]


def _recent_trading_dates(n: int) -> list[str]:
    """Return the last N trading dates (YYYY-MM-DD) by checking SPY data."""
    end = datetime.now()
    start = end - timedelta(days=n + 15)  # extra buffer for holidays
    spy = yf.download("SPY", start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
                       progress=False, auto_adjust=True)
    if spy.empty:
        raise RuntimeError("could not fetch SPY data to determine trading dates")
    dates = sorted(spy.index.strftime("%Y-%m-%d").tolist())
    return dates[-n:] if len(dates) >= n else dates


def fetch_all_markets() -> tuple[list[StockMarket], str]:
    """Fetch S&P 500 constituents with latest market data.

    Returns (stocks, most_recent_trading_date).
    """
    constituents = _fetch_sp500_constituents()
    tickers = constituents["ticker"].tolist()
    meta_by_ticker = {
        row["ticker"]: row for _, row in constituents.iterrows()
    }

    # Download latest 5 days of OHLCV for all tickers at once
    log.info("downloading market data for %d tickers...", len(tickers))
    data = yf.download(
        tickers,
        period="5d",
        progress=False,
        auto_adjust=True,
        group_by="ticker",
        threads=True,
    )

    if data.empty:
        raise RuntimeError("yfinance returned empty data")

    # Determine the most recent trading date
    trading_date = data.index[-1].strftime("%Y-%m-%d")
    prev_date = data.index[-2].strftime("%Y-%m-%d") if len(data.index) >= 2 else None

    stocks: list[StockMarket] = []
    for ticker in tickers:
        meta = meta_by_ticker.get(ticker, {})
        try:
            ticker_data = data if len(tickers) == 1 else data[ticker]

            latest = ticker_data.iloc[-1]
            close = float(latest["Close"])
            if close <= 0 or pd.isna(close):
                continue

            volume = int(latest["Volume"]) if not pd.isna(latest["Volume"]) else 0
            trading_value = close * volume

            # Single-day change
            change_pct = 0.0
            if prev_date and len(ticker_data) >= 2:
                prev_close = float(ticker_data.iloc[-2]["Close"])
                if prev_close > 0 and not pd.isna(prev_close):
                    change_pct = (close - prev_close) / prev_close * 100.0

            stocks.append(
                StockMarket(
                    ticker=ticker,
                    name=meta.get("name", ticker),
                    sector=meta.get("sector", ""),
                    sub_industry=meta.get("sub_industry", ""),
                    close=close,
                    open=float(latest["Open"]) if not pd.isna(latest["Open"]) else 0,
                    high=float(latest["High"]) if not pd.isna(latest["High"]) else 0,
                    low=float(latest["Low"]) if not pd.isna(latest["Low"]) else 0,
                    volume=volume,
                    trading_value=trading_value,
                    market_cap=0,  # filled below if available
                    change_pct=change_pct,
                )
            )
        except (KeyError, IndexError):
            continue

    # Fetch market caps in batch via yfinance Tickers
    _enrich_market_caps(stocks)

    log.info("fetched %d S&P 500 stocks for %s", len(stocks), trading_date)
    return stocks, trading_date


def _enrich_market_caps(stocks: list[StockMarket]) -> None:
    """Best-effort market cap enrichment using yfinance fast_info."""
    ticker_symbols = [s.ticker for s in stocks[:50]]  # top 50 only for speed
    try:
        tickers_obj = yf.Tickers(" ".join(ticker_symbols))
        for s in stocks:
            try:
                info = tickers_obj.tickers.get(s.ticker)
                if info and hasattr(info, "fast_info"):
                    mcap = getattr(info.fast_info, "market_cap", None)
                    if mcap and mcap > 0:
                        s.market_cap = float(mcap)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        log.warning("market cap enrichment failed: %s", exc)


def get_recent_trading_date() -> str:
    """Return the most recent US trading date."""
    dates = _recent_trading_dates(1)
    return dates[-1]


def get_past_trading_date(days_back: int) -> str:
    """Return the trading date N trading days ago."""
    dates = _recent_trading_dates(days_back + 1)
    if len(dates) <= days_back:
        raise RuntimeError(f"could not find {days_back} past trading days")
    return dates[-(days_back + 1)]
