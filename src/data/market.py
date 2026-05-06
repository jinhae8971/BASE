"""Market data layer — OHLCV, sector snapshot, factor panel.

Primary source: ``pykrx`` (offline-friendly KRX wrapper). Falls back to
``FinanceDataReader`` and finally to empty / synthetic data so the rest of the
pipeline keeps running in a degraded mode.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from common.logging import get_logger

from .universe import get_universe, sector_map

log = get_logger(__name__)


# ----------------------------------------------------------------------
# Price series
# ----------------------------------------------------------------------
@lru_cache(maxsize=512)
def fetch_price_series(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Daily OHLCV DataFrame indexed by datetime for ``ticker``.

    Columns: Open, High, Low, Close, Volume. Returns an empty DataFrame on
    failure rather than raising so callers can degrade gracefully.
    """
    try:
        from pykrx import stock  # type: ignore

        df = stock.get_market_ohlcv_by_date(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        df = df.rename(
            columns={
                "시가": "Open",
                "고가": "High",
                "저가": "Low",
                "종가": "Close",
                "거래량": "Volume",
            }
        )[["Open", "High", "Low", "Close", "Volume"]]
        df.index = pd.to_datetime(df.index)
        return df
    except Exception as e:
        log.debug("market.pykrx_failed", ticker=ticker, error=str(e))

    try:
        import FinanceDataReader as fdr  # type: ignore  # noqa: N813

        df = fdr.DataReader(ticker, start.isoformat(), end.isoformat())
        if df is None or df.empty:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        df.index = pd.to_datetime(df.index)
        return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        log.debug("market.fdr_failed", ticker=ticker, error=str(e))

    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


def fetch_close_panel(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """Close-price panel for many tickers (one column per ticker)."""
    out: dict[str, pd.Series] = {}
    for tkr in tickers:
        df = fetch_price_series(tkr, start, end)
        if not df.empty:
            out[tkr] = df["Close"].astype(float)
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out).sort_index()


def fetch_latest_prices(tickers: list[str], as_of: date | None = None) -> dict[str, float]:
    """Last available close price for each ticker (KRW)."""
    as_of = as_of or date.today()
    start = as_of - timedelta(days=14)
    panel = fetch_close_panel(tickers, start, as_of)
    if panel.empty:
        return {}
    last = panel.iloc[-1]
    return {t: float(v) for t, v in last.items() if pd.notna(v)}


# ----------------------------------------------------------------------
# Sector snapshot (used by the SectorAgent prompt)
# ----------------------------------------------------------------------
def fetch_sector_snapshot(as_of: date) -> dict[str, Any]:
    """Per-sector momentum / valuation summary."""
    end = as_of
    start = end - timedelta(days=400)
    universe = get_universe(as_of)
    smap = sector_map(as_of)

    sectors: dict[str, dict[str, float]] = {}
    counts: dict[str, int] = {}

    panel = fetch_close_panel([r["ticker"] for r in universe], start, end)
    if panel.empty:
        return {
            "as_of": as_of.isoformat(),
            "sectors": [],
            "_stub": True,
            "_reason": "no price data",
        }

    daily = panel.pct_change().dropna(how="all")
    mom_1m = panel.iloc[-1] / panel.iloc[-22] - 1 if len(panel) > 22 else pd.Series()
    mom_3m = panel.iloc[-1] / panel.iloc[-66] - 1 if len(panel) > 66 else pd.Series()
    vol_60 = daily.tail(60).std() * np.sqrt(252)

    for tkr in panel.columns:
        sec = smap.get(tkr, "기타")
        s = sectors.setdefault(
            sec,
            {"mom_1m": 0.0, "mom_3m": 0.0, "vol_60": 0.0},
        )
        counts[sec] = counts.get(sec, 0) + 1
        if tkr in mom_1m.index and pd.notna(mom_1m[tkr]):
            s["mom_1m"] += float(mom_1m[tkr])
        if tkr in mom_3m.index and pd.notna(mom_3m[tkr]):
            s["mom_3m"] += float(mom_3m[tkr])
        if tkr in vol_60.index and pd.notna(vol_60[tkr]):
            s["vol_60"] += float(vol_60[tkr])

    out = []
    for sec, agg in sectors.items():
        n = max(counts.get(sec, 1), 1)
        out.append(
            {
                "sector": sec,
                "members": n,
                "mom_1m": round(agg["mom_1m"] / n, 4),
                "mom_3m": round(agg["mom_3m"] / n, 4),
                "vol_60": round(agg["vol_60"] / n, 4),
            }
        )
    out.sort(key=lambda r: r["mom_3m"], reverse=True)
    return {"as_of": as_of.isoformat(), "sectors": out}


# ----------------------------------------------------------------------
# Factor panel (used by the QuantAgent prompt)
# ----------------------------------------------------------------------
def fetch_factor_panel(as_of: date) -> dict[str, Any]:
    """Cross-sectional factor panel, z-scored.

    Factors:
        - momentum (12-1m return)
        - lowvol   (-1 * 60-day std)
        - size     (-1 * log market cap)  → small-cap tilt; we use +log for "large"
        - quality  (placeholder using inverse vol; real ROE comes from fundamentals)
        - value    (placeholder using -1 * 6m return; real B/P comes from fundamentals)
    """
    universe = get_universe(as_of)
    tickers = [r["ticker"] for r in universe]
    end = as_of
    start = end - timedelta(days=400)

    panel = fetch_close_panel(tickers, start, end)
    if panel.empty or len(panel) < 30:
        return {
            "as_of": as_of.isoformat(),
            "universe_size": 0,
            "factors": ["value", "momentum", "quality", "lowvol", "size"],
            "rows": [],
            "_stub": True,
            "_reason": "insufficient price history",
        }

    daily = panel.pct_change()

    def _safe_ret(days: int) -> pd.Series:
        if len(panel) <= days:
            return pd.Series(dtype=float)
        return panel.iloc[-1] / panel.iloc[-days] - 1

    mom_12_1 = _safe_ret(252) - _safe_ret(22)
    ret_6m = _safe_ret(126)
    vol_60 = daily.tail(60).std() * np.sqrt(252)
    avg_dollar_vol = (panel * panel.diff().abs()).tail(20).mean()  # rough proxy

    # Real market cap if pykrx is reachable
    mcap = pd.Series(dtype=float)
    try:
        from pykrx import stock  # type: ignore

        ymd = as_of.strftime("%Y%m%d")
        cap_df = stock.get_market_cap_by_ticker(ymd)
        mcap = cap_df["시가총액"].astype(float)
    except Exception as e:
        log.debug("market.mcap_failed", error=str(e))

    name_lookup = {r["ticker"]: r.get("name") for r in universe}
    sec_lookup = {r["ticker"]: r.get("sector") for r in universe}

    def _z(s: pd.Series) -> pd.Series:
        s = s.dropna()
        if s.empty or s.std() == 0:
            return pd.Series(0.0, index=s.index)
        return (s - s.mean()) / s.std()

    momentum = _z(mom_12_1)
    lowvol = _z(-vol_60)
    size = _z(np.log(mcap.replace(0, np.nan))) if not mcap.empty else pd.Series(dtype=float)
    quality = _z(-vol_60 / vol_60.replace(0, np.nan))  # weak placeholder, will be replaced
    value = _z(-ret_6m)
    liquidity = _z(np.log(avg_dollar_vol.replace(0, np.nan)))

    rows: list[dict[str, Any]] = []
    for tkr in tickers:
        rows.append(
            {
                "ticker": tkr,
                "name": name_lookup.get(tkr),
                "sector": sec_lookup.get(tkr),
                "value": float(value.get(tkr, 0.0)) if not value.empty else 0.0,
                "momentum": float(momentum.get(tkr, 0.0)) if not momentum.empty else 0.0,
                "quality": float(quality.get(tkr, 0.0)) if not quality.empty else 0.0,
                "lowvol": float(lowvol.get(tkr, 0.0)) if not lowvol.empty else 0.0,
                "size": float(size.get(tkr, 0.0)) if not size.empty else 0.0,
                "liquidity": float(liquidity.get(tkr, 0.0)) if not liquidity.empty else 0.0,
            }
        )
    return {
        "as_of": as_of.isoformat(),
        "universe_size": len(rows),
        "factors": ["value", "momentum", "quality", "lowvol", "size", "liquidity"],
        "rows": rows,
    }


# ----------------------------------------------------------------------
# Benchmark
# ----------------------------------------------------------------------
def fetch_benchmark_series(start: date, end: date) -> pd.Series:
    """KOSPI close series indexed by date (returns empty Series on failure)."""
    try:
        import FinanceDataReader as fdr  # type: ignore  # noqa: N813

        df = fdr.DataReader("KS11", start.isoformat(), end.isoformat())
        if df is not None and not df.empty:
            df.index = pd.to_datetime(df.index)
            return df["Close"].astype(float).rename("KOSPI")
    except Exception as e:
        log.debug("market.benchmark_fdr_failed", error=str(e))
    try:
        from pykrx import stock  # type: ignore

        df = stock.get_index_ohlcv_by_date(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "1001"
        )
        if df is not None and not df.empty:
            df.index = pd.to_datetime(df.index)
            return df["종가"].astype(float).rename("KOSPI")
    except Exception as e:
        log.debug("market.benchmark_pykrx_failed", error=str(e))
    return pd.Series(dtype=float, name="KOSPI")
