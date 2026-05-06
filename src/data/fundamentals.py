"""Fundamentals — multiples and financial ratios from DART (OpenDartReader).

Strategy:
    1. Try OpenDartReader (uses ``DART_API_KEY``) for the latest annual / quarterly
       financial statements.
    2. Fall back to pykrx multiples (PER/PBR/EPS/BPS/DIV) when DART access is
       unavailable.
    3. Final fallback: empty list with a ``_stub`` marker so the LLM agent can
       still respond gracefully.
"""
from __future__ import annotations

import contextlib
from datetime import date
from typing import Any

import pandas as pd

from common.config import get_env
from common.logging import get_logger

from .universe import get_universe

log = get_logger(__name__)


# ----------------------------------------------------------------------
def _pykrx_fundamentals(as_of: date) -> pd.DataFrame:
    """PER/PBR/EPS/BPS/DIV/DPS for the universe via pykrx (best-effort)."""
    try:
        from pykrx import stock  # type: ignore

        ymd = as_of.strftime("%Y%m%d")
        df = stock.get_market_fundamental_by_ticker(ymd)
        if df is None:
            return pd.DataFrame()
        return df
    except Exception as e:
        log.debug("fundamentals.pykrx_failed", error=str(e))
        return pd.DataFrame()


def _dart_recent_financials(ticker: str, year: int) -> dict[str, float]:
    env = get_env()
    if not env.dart_api_key:
        return {}
    try:
        import OpenDartReader  # type: ignore

        dart = OpenDartReader(env.dart_api_key)
        fs = dart.finstate(ticker, year)
        if fs is None or fs.empty:
            return {}
        # Pick the most recent reprt_code row per account
        snap: dict[str, float] = {}
        for _, row in fs.iterrows():
            name = str(row.get("account_nm", ""))
            try:
                amt = float(str(row.get("thstrm_amount", "0")).replace(",", ""))
            except ValueError:
                amt = 0.0
            snap[name] = amt
        return snap
    except Exception as e:
        log.debug("fundamentals.dart_failed", ticker=ticker, error=str(e))
        return {}


def fetch_value_candidates(as_of: date, top_n: int = 30) -> dict[str, Any]:
    """Pre-filter the universe for the ValueAgent.

    Returns a list of candidates ordered by a simple value composite
    (low PER + low PBR + above-median dividend yield).
    """
    universe = get_universe(as_of)
    fund = _pykrx_fundamentals(as_of)
    candidates: list[dict[str, Any]] = []

    for row in universe:
        tkr = row["ticker"]
        snap = {
            "ticker": tkr,
            "name": row.get("name"),
            "sector": row.get("sector"),
        }
        if not fund.empty and tkr in fund.index:
            with contextlib.suppress(Exception):
                snap.update(
                    {
                        "per": float(fund.loc[tkr, "PER"]),
                        "pbr": float(fund.loc[tkr, "PBR"]),
                        "eps": float(fund.loc[tkr, "EPS"]),
                        "bps": float(fund.loc[tkr, "BPS"]),
                        "dividend_yield": float(fund.loc[tkr, "DIV"]) / 100.0,
                    }
                )
        # Optional DART augmentation — only fire when key is present
        # (kept off by default so we don't burn rate limits in the daily loop)
        # if get_env().dart_api_key:
        #     snap["dart"] = _dart_recent_financials(tkr, as_of.year - 1)
        candidates.append(snap)

    # Score: lower PER+PBR is better, dividend yield is bonus.
    def _score(c: dict[str, Any]) -> float:
        per = c.get("per") or 1e6
        pbr = c.get("pbr") or 1e6
        dy = c.get("dividend_yield") or 0.0
        if per <= 0:
            per = 1e6
        if pbr <= 0:
            pbr = 1e6
        return -(0.6 / per + 0.3 / pbr + dy)

    scored = sorted(candidates, key=_score)[:top_n]
    return {
        "as_of": as_of.isoformat(),
        "candidates": scored,
        "n_total_universe": len(universe),
    }


# Optional convenience wrapper for ad-hoc lookups
def fetch_dart_summary(ticker: str, year: int) -> dict[str, float]:
    return _dart_recent_financials(ticker, year)
