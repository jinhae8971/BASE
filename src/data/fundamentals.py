"""Financial fundamentals — pykrx (KRX) + OpenDartReader (DART).

Phase 1:
- pykrx provides PER, PBR, EPS, DIV for all KOSPI stocks in real-time
- OpenDartReader adds ROE, D/E ratio from DART filings (annual/quarterly)
- Value candidates = KOSPI200 stocks filtered by PBR < 2 and PER > 0
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from common.config import get_env
from common.logging import get_logger
from data.market import _last_trading_day, _safe_float

log = get_logger(__name__)

KOSPI200_INDEX = "1028"
MAX_CANDIDATES = 60


def fetch_value_candidates(as_of: dt.date | None = None) -> dict[str, Any]:
    as_of = as_of or dt.date.today()
    date_str = _last_trading_day(as_of)

    try:
        from pykrx import stock  # type: ignore

        tickers = stock.get_index_portfolio_deposit_file(KOSPI200_INDEX)
        fund = _get_fundamentals(stock, date_str)
        caps = _get_caps(stock, date_str)
        prices = _get_prices(stock, date_str)

        # Optional: DART for ROE / D/E
        dart_data = _fetch_dart_roe(tickers) if get_env().dart_api_key else {}

        candidates = []
        for tkr in tickers:
            if tkr not in fund.index:
                continue

            per = _safe_float(fund, tkr, "PER")
            pbr = _safe_float(fund, tkr, "PBR")
            eps = _safe_float(fund, tkr, "EPS")
            bps = _safe_float(fund, tkr, "BPS")
            div = _safe_float(fund, tkr, "DIV")

            # Basic value filter: positive PER and reasonable PBR
            if per is None or per <= 0 or pbr is None or pbr <= 0:
                continue
            if pbr > 3.0:
                continue

            price = _safe_float(prices, tkr, "종가")
            cap = _safe_float(caps, tkr, "시가총액")

            try:
                name = stock.get_market_ticker_name(tkr)
            except Exception:
                name = tkr

            dart_info = dart_data.get(tkr, {})

            candidates.append(
                {
                    "ticker": tkr,
                    "name": name,
                    "per_fwd": round(per, 2),
                    "pbr": round(pbr, 2),
                    "eps": int(eps) if eps else None,
                    "bps": int(bps) if bps else None,
                    "dividend_yield": round(div / 100, 4) if div else None,
                    "current_price": int(price) if price else None,
                    "market_cap_bn": round(cap / 1e8, 1) if cap else None,
                    "roe": dart_info.get("roe"),
                    "de_ratio": dart_info.get("de_ratio"),
                    # Graham intrinsic value estimate: sqrt(22.5 * EPS * BPS)
                    "intrinsic_value": _graham_value(eps, bps),
                }
            )

        # Sort by composite score: low PBR + moderate PER
        candidates.sort(key=lambda x: (x["pbr"], x["per_fwd"]))
        candidates = candidates[:MAX_CANDIDATES]

        return {"as_of": as_of.isoformat(), "candidates": candidates}

    except Exception as exc:
        log.warning("fundamentals.fetch_failed", error=str(exc))
        return {"as_of": as_of.isoformat(), "candidates": [], "_stub": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_fundamentals(stock: Any, date_str: str) -> Any:
    try:
        return stock.get_market_fundamental_by_ticker(date_str, market="KOSPI")
    except Exception:
        import pandas as pd
        return pd.DataFrame()


def _get_caps(stock: Any, date_str: str) -> Any:
    try:
        return stock.get_market_cap_by_ticker(date_str, market="KOSPI")
    except Exception:
        import pandas as pd
        return pd.DataFrame()


def _get_prices(stock: Any, date_str: str) -> Any:
    try:
        return stock.get_market_ohlcv_by_ticker(date_str, market="KOSPI")
    except Exception:
        import pandas as pd
        return pd.DataFrame()


def _graham_value(eps: float | None, bps: float | None) -> int | None:
    if not eps or not bps or eps <= 0 or bps <= 0:
        return None
    import math
    return int(math.sqrt(22.5 * eps * bps))


def _fetch_dart_roe(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch ROE and D/E from OpenDartReader for KOSPI200 tickers."""
    dart_key = get_env().dart_api_key
    if not dart_key:
        return {}
    try:
        import OpenDartReader  # type: ignore
        dart = OpenDartReader.OpenDartReader(dart_key)
        result: dict[str, dict[str, Any]] = {}
        current_year = dt.date.today().year - 1  # prior fiscal year

        for tkr in tickers[:30]:  # limit API calls
            try:
                fs = dart.finstate(tkr, current_year, reprt_code="11011")  # annual
                if fs is None or fs.empty:
                    continue
                equity = _dart_value(fs, "자본총계")
                liab = _dart_value(fs, "부채총계")
                net_income = _dart_value(fs, "당기순이익")

                if equity and equity > 0:
                    roe = round(net_income / equity * 100, 2) if net_income else None
                    de = round(liab / equity * 100, 2) if liab else None
                    result[tkr] = {"roe": roe, "de_ratio": de}
            except Exception:
                continue
        return result
    except Exception as exc:
        log.debug("fundamentals.dart_failed", error=str(exc))
        return {}


def _dart_value(fs: Any, account: str) -> float | None:
    try:
        row = fs[fs["account_nm"] == account]
        if row.empty:
            return None
        return float(str(row.iloc[0]["thstrm_amount"]).replace(",", ""))
    except Exception:
        return None
