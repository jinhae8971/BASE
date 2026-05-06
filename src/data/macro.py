"""Macro indicators — ECOS (한은) + FinanceDataReader + yfinance.

Phase 1: live data from ECOS API for Korean rates, FinanceDataReader for
global equity/fx/commodities. Falls back to empty dict on any network error
so the pipeline continues in stub mode.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import FinanceDataReader as fdr

from common.config import get_env
from common.logging import get_logger

log = get_logger(__name__)

ECOS_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"

# (statCode, itemCode) pairs for ECOS
ECOS_SERIES = {
    "kr_base_rate": ("722Y001", "0101000"),   # 기준금리
    "kr_cd_91d":    ("817Y002", "010190000"), # CD 91일
    "kr_m2_mom":    ("101Y004", "BBGS00"),    # M2 (전월비)
}

# FinanceDataReader / Yahoo Finance tickers
FDR_TICKERS: dict[str, tuple[str, str]] = {
    "kospi":    ("KS11",    "equity"),
    "sp500":    ("^GSPC",   "equity"),
    "vix":      ("^VIX",    "equity"),
    "us10y":    ("^TNX",    "rates"),
    "usd_krw":  ("USD/KRW", "fx"),
    "oil_wti":  ("CL=F",    "commodities"),
    "gold":     ("GC=F",    "commodities"),
}


def fetch_macro_snapshot(as_of: dt.date | None = None) -> dict[str, Any]:
    as_of = as_of or dt.date.today()
    snapshot: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "rates": {},
        "fx": {},
        "commodities": {},
        "equity": {},
        "credit": {},
        "leading": {},
        "events_next_7d": [],
    }

    # --- FinanceDataReader (global) ---
    try:
        _fill_fdr(snapshot, as_of)
    except Exception as exc:
        log.warning("macro.fdr_failed", error=str(exc))

    # --- ECOS (Korean indicators) ---
    ecos_key = get_env().ecos_api_key
    if ecos_key:
        try:
            kr_rates = _fetch_ecos(ecos_key, as_of)
            snapshot["rates"].update(kr_rates)
        except Exception as exc:
            log.warning("macro.ecos_failed", error=str(exc))

    return snapshot


# ---------------------------------------------------------------------------
# FinanceDataReader helpers
# ---------------------------------------------------------------------------

def _fill_fdr(snapshot: dict[str, Any], as_of: dt.date) -> None:
    lookback = as_of - dt.timedelta(days=100)
    start_str = lookback.strftime("%Y-%m-%d")
    end_str = as_of.strftime("%Y-%m-%d")

    for key, (sym, bucket) in FDR_TICKERS.items():
        try:
            df = fdr.DataReader(sym, start_str, end_str)
            if df.empty:
                continue
            close_col = "Close" if "Close" in df.columns else df.columns[3]
            series = df[close_col].dropna()
            if series.empty:
                continue
            last = float(series.iloc[-1])
            snapshot[bucket][key] = round(last, 4)

            # 3-month momentum for equity tickers
            if bucket == "equity" and len(series) >= 63:
                mom = float(series.iloc[-1] / series.iloc[-63] - 1)
                snapshot[bucket][f"{key}_mom_3m"] = round(mom, 4)
        except Exception as exc:
            log.debug("macro.fdr_ticker_failed", sym=sym, error=str(exc))


# ---------------------------------------------------------------------------
# ECOS helpers
# ---------------------------------------------------------------------------

def _fetch_ecos(api_key: str, as_of: dt.date) -> dict[str, float | None]:
    end_ym = as_of.strftime("%Y%m")
    start_ym = (as_of.replace(day=1) - dt.timedelta(days=60)).strftime("%Y%m")
    result: dict[str, float | None] = {}

    for name, (stat_code, item_code) in ECOS_SERIES.items():
        url = (
            f"{ECOS_BASE}/{api_key}/json/kr/1/5"
            f"/{stat_code}/M/{start_ym}/{end_ym}/{item_code}"
        )
        try:
            r = httpx.get(url, timeout=10)
            r.raise_for_status()
            rows = r.json().get("StatisticSearch", {}).get("row", [])
            if rows:
                result[name] = float(rows[-1]["DATA_VALUE"])
        except Exception as exc:
            log.debug("macro.ecos_series_failed", series=name, error=str(exc))

    return result
