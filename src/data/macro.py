"""Macro indicators — yfinance + ECOS + FRED.

The MacroAgent doesn't need pinpoint accuracy here — it needs a *consistent*
snapshot across rates, FX, commodities, equity momentum, and credit. We pull
each block independently so partial failures still produce a usable context.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from common.config import get_env
from common.logging import get_logger

log = get_logger(__name__)


def _yf_last(ticker: str, days: int = 30) -> float | None:
    try:
        import yfinance as yf  # type: ignore

        end = date.today()
        start = end - timedelta(days=days)
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
        if df is not None and not df.empty:
            return float(df["Close"].iloc[-1])
    except Exception as e:
        log.debug("macro.yf_failed", ticker=ticker, error=str(e))
    return None


def _yf_momentum(ticker: str, days: int = 90) -> float | None:
    try:
        import yfinance as yf  # type: ignore

        end = date.today()
        start = end - timedelta(days=days + 14)
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or df.empty or len(df) < days // 2:
            return None
        last = float(df["Close"].iloc[-1])
        first = float(df["Close"].iloc[0])
        if first == 0:
            return None
        return last / first - 1
    except Exception as e:
        log.debug("macro.yf_mom_failed", ticker=ticker, error=str(e))
        return None


def _ecos(stat_code: str) -> float | None:
    """Fetch the latest value of a Bank of Korea ECOS series."""
    env = get_env()
    if not env.ecos_api_key:
        return None
    try:
        import httpx

        url = (
            f"https://ecos.bok.or.kr/api/StatisticSearch/{env.ecos_api_key}"
            f"/json/kr/1/1/{stat_code}/D"
        )
        r = httpx.get(url, timeout=8)
        r.raise_for_status()
        data = r.json()
        rows = (data.get("StatisticSearch") or {}).get("row", [])
        if not rows:
            return None
        return float(rows[-1]["DATA_VALUE"])
    except Exception as e:
        log.debug("macro.ecos_failed", code=stat_code, error=str(e))
        return None


def fetch_macro_snapshot(as_of: date) -> dict[str, Any]:
    """Compact snapshot for the MacroAgent prompt."""
    snap: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "rates": {},
        "fx": {},
        "commodities": {},
        "equity": {},
        "credit": {},
        "leading": {},
        "events_next_7d": [],
    }

    # --- Rates -------------------------------------------------------
    us10 = _yf_last("^TNX")  # x10 → percent
    if us10 is not None:
        snap["rates"]["us_10y"] = round(us10 / 10.0, 3)
    us2 = _yf_last("^IRX")
    if us2 is not None:
        snap["rates"]["us_3m"] = round(us2 / 10.0, 3)
    kr3y = _ecos("722Y001")  # KR 국채 3년
    if kr3y is not None:
        snap["rates"]["kr_3y"] = kr3y

    # --- FX ----------------------------------------------------------
    usdkrw = _yf_last("KRW=X")
    if usdkrw is not None:
        snap["fx"]["usd_krw"] = round(usdkrw, 2)
    dxy = _yf_last("DX-Y.NYB")
    if dxy is not None:
        snap["fx"]["dxy"] = round(dxy, 2)

    # --- Commodities -------------------------------------------------
    snap["commodities"] = {
        "oil_wti": _yf_last("CL=F"),
        "copper": _yf_last("HG=F"),
        "gold": _yf_last("GC=F"),
    }

    # --- Equity / sentiment ------------------------------------------
    snap["equity"] = {
        "kospi": _yf_last("^KS11"),
        "kospi_mom_3m": _yf_momentum("^KS11", 90),
        "spx_mom_3m": _yf_momentum("^GSPC", 90),
        "vix": _yf_last("^VIX"),
    }

    # --- Credit (HY OAS proxy via HYG ETF momentum) ------------------
    hyg_mom = _yf_momentum("HYG", 30)
    if hyg_mom is not None:
        snap["credit"]["hyg_mom_1m"] = round(hyg_mom, 4)

    # --- Cleanup -----------------------------------------------------
    snap["commodities"] = {k: v for k, v in snap["commodities"].items() if v is not None}
    snap["equity"] = {k: v for k, v in snap["equity"].items() if v is not None}

    if not any([snap["rates"], snap["fx"], snap["commodities"], snap["equity"]]):
        snap["_stub"] = True
        snap["_reason"] = "no upstream data sources reachable"
    return snap
