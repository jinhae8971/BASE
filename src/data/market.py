"""Market data — pykrx (KRX) + FinanceDataReader fallback.

Phase 1:
- fetch_price_series: single-stock OHLCV via pykrx
- fetch_sector_snapshot: KOSPI200 sector fundamentals aggregated by 업종
- fetch_factor_panel: cross-sectional z-score factors for quant agent
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from common.logging import get_logger

log = get_logger(__name__)

KOSPI200_INDEX = "1028"


# ---------------------------------------------------------------------------
# Price series
# ---------------------------------------------------------------------------

def fetch_price_series(ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    """OHLCV DataFrame indexed by date (columns: Open High Low Close Volume)."""
    try:
        from pykrx import stock  # type: ignore
        s = start.strftime("%Y%m%d")
        e = end.strftime("%Y%m%d")
        df = stock.get_market_ohlcv_by_date(s, e, ticker)
        if df.empty:
            return _empty_ohlcv()
        rename = {"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"}
        return df.rename(columns=rename)[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as exc:
        log.warning("market.price_series_failed", ticker=ticker, error=str(exc))
        return _empty_ohlcv()


def fetch_prices_batch(tickers: list[str], as_of: dt.date) -> dict[str, float]:
    """Return {ticker: last_close} for a list of tickers (single day lookup)."""
    try:
        from pykrx import stock  # type: ignore
        date_str = _last_trading_day(as_of)
        df = stock.get_market_ohlcv_by_ticker(date_str, market="KOSPI")
        col = "종가" if "종가" in df.columns else "Close"
        return {t: float(df.loc[t, col]) for t in tickers if t in df.index}
    except Exception as exc:
        log.warning("market.prices_batch_failed", error=str(exc))
        return {}


# ---------------------------------------------------------------------------
# Sector snapshot
# ---------------------------------------------------------------------------

def fetch_sector_snapshot(as_of: dt.date | None = None) -> dict[str, Any]:
    as_of = as_of or dt.date.today()
    date_str = _last_trading_day(as_of)

    try:
        from pykrx import stock  # type: ignore

        tickers = stock.get_index_portfolio_deposit_file(KOSPI200_INDEX)
        fund = _safe_fundamentals(stock, date_str)
        caps = _safe_caps(stock, date_str)
        sec_df = _safe_sectors(stock, date_str)

        # Aggregate by sector
        sector_map: dict[str, dict[str, Any]] = {}
        for tkr in tickers:
            sec_name = _sector_name(sec_df, tkr)
            if sec_name not in sector_map:
                sector_map[sec_name] = {
                    "per_vals": [], "pbr_vals": [], "div_vals": [], "cap_total": 0.0
                }
            entry = sector_map[sec_name]
            if tkr in fund.index:
                per = _safe_float(fund, tkr, "PER")
                pbr = _safe_float(fund, tkr, "PBR")
                div = _safe_float(fund, tkr, "DIV")
                if per and 0 < per < 200:
                    entry["per_vals"].append(per)
                if pbr and pbr > 0:
                    entry["pbr_vals"].append(pbr)
                if div and div >= 0:
                    entry["div_vals"].append(div)
            if tkr in caps.index:
                cap = _safe_float(caps, tkr, "시가총액")
                if cap:
                    entry["cap_total"] += cap

        sectors = [
            {
                "name": name,
                "avg_per": round(float(np.mean(v["per_vals"])), 2) if v["per_vals"] else None,
                "avg_pbr": round(float(np.mean(v["pbr_vals"])), 2) if v["pbr_vals"] else None,
                "avg_div": round(float(np.mean(v["div_vals"])), 2) if v["div_vals"] else None,
                "market_cap_bn": round(v["cap_total"] / 1e8, 1),
            }
            for name, v in sector_map.items()
        ]
        sectors.sort(key=lambda x: x["market_cap_bn"], reverse=True)

        return {"as_of": as_of.isoformat(), "sectors": sectors}

    except Exception as exc:
        log.warning("market.sector_snapshot_failed", error=str(exc))
        return {"as_of": as_of.isoformat(), "sectors": [], "_stub": True}


# ---------------------------------------------------------------------------
# Factor panel
# ---------------------------------------------------------------------------

def fetch_factor_panel(as_of: dt.date | None = None) -> dict[str, Any]:
    as_of = as_of or dt.date.today()
    date_str = _last_trading_day(as_of)

    try:
        from pykrx import stock  # type: ignore

        tickers = stock.get_index_portfolio_deposit_file(KOSPI200_INDEX)
        fund = _safe_fundamentals(stock, date_str)
        caps = _safe_caps(stock, date_str)

        # 3-month lookback for momentum
        lookback = as_of - dt.timedelta(days=100)
        look_str = _last_trading_day(lookback)
        ohlcv_now = _safe_ohlcv(stock, date_str)
        ohlcv_old = _safe_ohlcv(stock, look_str)

        rows = []
        for tkr in tickers:
            row: dict[str, Any] = {"ticker": tkr}

            # Value: PBR (lower = value)
            pbr = _safe_float(fund, tkr, "PBR") if tkr in fund.index else None
            per = _safe_float(fund, tkr, "PER") if tkr in fund.index else None
            row["pbr"] = pbr
            row["per"] = per

            # Size: log market cap
            cap = _safe_float(caps, tkr, "시가총액") if tkr in caps.index else None
            row["market_cap"] = cap

            # Momentum: 3m price return
            p_now = _safe_float(ohlcv_now, tkr, "종가") if tkr in ohlcv_now.index else None
            p_old = _safe_float(ohlcv_old, tkr, "종가") if tkr in ohlcv_old.index else None
            row["mom_3m"] = float(p_now / p_old - 1) if p_now and p_old and p_old > 0 else None

            rows.append(row)

        # Z-score each factor
        rows = _zscore_factor(rows, "pbr", invert=True)    # low PBR = high value
        rows = _zscore_factor(rows, "per", invert=True)
        rows = _zscore_factor(rows, "market_cap", invert=True)  # small = high score
        rows = _zscore_factor(rows, "mom_3m", invert=False)

        return {
            "as_of": as_of.isoformat(),
            "universe_size": len(rows),
            "factors": ["value", "momentum", "size"],
            "rows": rows,
        }

    except Exception as exc:
        log.warning("market.factor_panel_failed", error=str(exc))
        return {
            "as_of": as_of.isoformat(),
            "universe_size": 0,
            "factors": ["value", "momentum", "quality", "lowvol", "size"],
            "rows": [],
            "_stub": True,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _last_trading_day(d: dt.date) -> str:
    """Return the most recent weekday on or before d as YYYYMMDD string."""
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d -= dt.timedelta(days=1)
    return d.strftime("%Y%m%d")


def _empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


def _safe_fundamentals(stock: Any, date_str: str) -> pd.DataFrame:
    try:
        return stock.get_market_fundamental_by_ticker(date_str, market="KOSPI")
    except Exception:
        return pd.DataFrame()


def _safe_caps(stock: Any, date_str: str) -> pd.DataFrame:
    try:
        return stock.get_market_cap_by_ticker(date_str, market="KOSPI")
    except Exception:
        return pd.DataFrame()


def _safe_sectors(stock: Any, date_str: str) -> pd.DataFrame:
    try:
        return stock.get_market_sector_classifications(date_str, "KOSPI")
    except Exception:
        return pd.DataFrame()


def _safe_ohlcv(stock: Any, date_str: str) -> pd.DataFrame:
    try:
        return stock.get_market_ohlcv_by_ticker(date_str, market="KOSPI")
    except Exception:
        return pd.DataFrame()


def _safe_float(df: pd.DataFrame, ticker: str, col: str) -> float | None:
    try:
        v = df.loc[ticker, col]
        if pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def _sector_name(sec_df: pd.DataFrame, ticker: str) -> str:
    if sec_df.empty or ticker not in sec_df.index:
        return "기타"
    try:
        return str(sec_df.loc[ticker, "업종명"])
    except Exception:
        return "기타"


def _zscore_factor(rows: list[dict], key: str, *, invert: bool) -> list[dict]:
    vals = [r[key] for r in rows if r.get(key) is not None]
    if len(vals) < 5:
        for r in rows:
            r[f"{key}_z"] = 0.0
        return rows
    mu = float(np.mean(vals))
    sigma = float(np.std(vals)) or 1.0
    for r in rows:
        v = r.get(key)
        z = float((v - mu) / sigma) if v is not None else 0.0
        r[f"{key}_z"] = round(-z if invert else z, 3)
    return rows
