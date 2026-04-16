"""Dow Jones 30 market data client using yfinance + Wikipedia for constituents."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from .logging_setup import get_logger
from .models import StockMarket

log = get_logger(__name__)

DJIA_WIKI_URL = "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _fetch_djia_constituents() -> pd.DataFrame:
    """Scrape current Dow 30 list from Wikipedia."""
    tables = pd.read_html(DJIA_WIKI_URL)
    # Find the table with ticker/symbol column
    df = None
    for t in tables:
        cols_lower = [str(c).lower() for c in t.columns]
        if any("symbol" in c or "ticker" in c for c in cols_lower):
            df = t
            break
    if df is None:
        raise RuntimeError("could not find DJIA constituents table on Wikipedia")

    col_map = {}
    for c in df.columns:
        cl = str(c).lower().strip()
        if cl in ("symbol", "ticker"):
            col_map[c] = "ticker"
        elif cl in ("company",):
            col_map[c] = "name"
        elif "industry" in cl:
            col_map[c] = "sector"
    df = df.rename(columns=col_map)

    if "ticker" not in df.columns:
        raise RuntimeError("ticker column not found in DJIA Wikipedia table")
    if "name" not in df.columns:
        df["name"] = df["ticker"]
    if "sector" not in df.columns:
        df["sector"] = ""

    df["ticker"] = df["ticker"].astype(str).str.replace(".", "-", regex=False).str.strip()
    log.info("fetched %d DJIA constituents from Wikipedia", len(df))
    return df[["ticker", "name", "sector"]]


def _recent_trading_dates(n: int) -> list[str]:
    """Return the last N trading dates by checking DIA (Dow ETF) data."""
    end = datetime.now()
    start = end - timedelta(days=n + 15)
    dia = yf.download("DIA", start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
                       progress=False, auto_adjust=True)
    if dia.empty:
        raise RuntimeError("could not fetch DIA data to determine trading dates")
    dates = sorted(dia.index.strftime("%Y-%m-%d").tolist())
    return dates[-n:] if len(dates) >= n else dates


def fetch_all_markets() -> tuple[list[StockMarket], str]:
    """Fetch Dow 30 constituents with latest market data."""
    constituents = _fetch_djia_constituents()
    tickers = constituents["ticker"].tolist()
    meta_by_ticker = {row["ticker"]: row for _, row in constituents.iterrows()}

    log.info("downloading market data for %d tickers...", len(tickers))
    data = yf.download(
        tickers, period="5d", progress=False,
        auto_adjust=True, group_by="ticker", threads=True,
    )

    if data.empty:
        raise RuntimeError("yfinance returned empty data")

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
                    close=close,
                    open=float(latest["Open"]) if not pd.isna(latest["Open"]) else 0,
                    high=float(latest["High"]) if not pd.isna(latest["High"]) else 0,
                    low=float(latest["Low"]) if not pd.isna(latest["Low"]) else 0,
                    volume=volume,
                    trading_value=trading_value,
                    market_cap=0,
                    change_pct=change_pct,
                )
            )
        except (KeyError, IndexError):
            continue

    _enrich_market_caps(stocks)
    log.info("fetched %d Dow 30 stocks for %s", len(stocks), trading_date)
    return stocks, trading_date


def _enrich_market_caps(stocks: list[StockMarket]) -> None:
    ticker_symbols = [s.ticker for s in stocks]
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
    return _recent_trading_dates(1)[-1]


def get_past_trading_date(days_back: int) -> str:
    dates = _recent_trading_dates(days_back + 1)
    if len(dates) <= days_back:
        raise RuntimeError(f"could not find {days_back} past trading days")
    return dates[-(days_back + 1)]
