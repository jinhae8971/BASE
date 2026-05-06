"""Korea Investment & Securities (KIS) Open API client.

Reference: https://apiportal.koreainvestment.com/

A lean wrapper focused on what MAI-System needs:
    - OAuth token issuance + caching
    - Domestic stock quote (single + bulk)
    - Domestic stock cash buy/sell order
    - Account balance + positions parsing
    - Open-order inquiry / cancellation

Set ``KIS_ENV=paper`` (default) for the simulated environment. The base URL
and TR IDs switch automatically.
"""
from __future__ import annotations

import contextlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from common.config import get_env
from common.logging import get_logger
from common.types import ExecutionResult, Order, Side

log = get_logger(__name__)

BASE_URLS = {
    "paper": "https://openapivts.koreainvestment.com:29443",
    "live": "https://openapi.koreainvestment.com:9443",
}
TOKEN_CACHE = Path(".kis_token.json")


class KISClient:
    def __init__(self) -> None:
        env = get_env()
        self.app_key = env.kis_app_key
        self.app_secret = env.kis_app_secret
        self.account_no = env.kis_account_no
        self.env = (env.kis_env or "paper").lower()
        self.base_url = BASE_URLS.get(self.env, BASE_URLS["paper"])
        self._token: str | None = None
        self._token_expires_at: float = 0

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _load_cached_token(self) -> None:
        if not TOKEN_CACHE.exists():
            return
        try:
            data = json.loads(TOKEN_CACHE.read_text())
            if data.get("env") == self.env and data.get("expires_at", 0) > time.time() + 60:
                self._token = data["token"]
                self._token_expires_at = data["expires_at"]
        except Exception:
            pass

    def _save_cached_token(self) -> None:
        with contextlib.suppress(Exception):
            TOKEN_CACHE.write_text(
                json.dumps(
                    {
                        "env": self.env,
                        "token": self._token,
                        "expires_at": self._token_expires_at,
                    }
                )
            )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _issue_token(self) -> str:
        if not self.app_key or not self.app_secret:
            raise RuntimeError("KIS_APP_KEY / KIS_APP_SECRET not set")
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        r = httpx.post(url, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        # KIS tokens expire after ~24h; keep a 23h safety margin
        self._token_expires_at = time.time() + 23 * 3600
        self._save_cached_token()
        return self._token

    def token(self) -> str:
        if self._token is None:
            self._load_cached_token()
        if self._token is None or time.time() >= self._token_expires_at:
            return self._issue_token()
        return self._token

    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------
    def _headers(self, tr_id: str) -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _split_account(self) -> tuple[str, str]:
        if not self.account_no:
            return "", "01"
        if "-" in self.account_no:
            head, tail = self.account_no.split("-", 1)
            return head, tail
        return self.account_no[:8], (self.account_no[8:] or "01")

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
    def get_price(self, ticker: str) -> float:
        """Current price (KRW)."""
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        r = httpx.get(url, params=params, headers=self._headers("FHKST01010100"), timeout=10)
        r.raise_for_status()
        data = r.json()
        return float(data["output"]["stck_prpr"])

    def get_prices(self, tickers: list[str]) -> dict[str, float]:
        """Bulk current prices (sequential — KIS has no batch quote endpoint)."""
        out: dict[str, float] = {}
        for t in tickers:
            try:
                out[t] = self.get_price(t)
            except Exception as e:
                log.warning("kis.price_failed", ticker=t, error=str(e))
        return out

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
    def get_orderbook(self, ticker: str) -> dict[str, Any]:
        """Top-of-book bid/ask for ``ticker``.

        Returns ``{"bid": float, "ask": float, "mid": float, "spread_bps": float}``
        — values are 0 when unreachable.
        """
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        try:
            r = httpx.get(
                url, params=params, headers=self._headers("FHKST01010200"), timeout=10
            )
            r.raise_for_status()
            o = (r.json().get("output1") or {})
            bid = float(o.get("bidp1") or 0)
            ask = float(o.get("askp1") or 0)
            mid = (bid + ask) / 2 if (bid and ask) else max(bid, ask)
            spread_bps = (ask - bid) / mid * 10_000 if mid > 0 else 0.0
            return {"bid": bid, "ask": ask, "mid": mid, "spread_bps": spread_bps}
        except Exception as e:
            log.warning("kis.orderbook_failed", ticker=ticker, error=str(e))
            return {"bid": 0.0, "ask": 0.0, "mid": 0.0, "spread_bps": 0.0}

    def get_orderbooks(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        return {t: self.get_orderbook(t) for t in tickers}

    # ------------------------------------------------------------------
    # Trading
    # ------------------------------------------------------------------
    def place_order(self, order: Order) -> ExecutionResult:
        from .tick_size import snap_to_tick

        tr_id = self._order_tr_id(order.side)
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        cano, prdt = self._split_account()
        # Snap to KRX tick size — buys snap down, sells snap up.
        snap_side = "down" if order.side is Side.BUY else "up"
        unpr = (
            snap_to_tick(order.price, side=snap_side)
            if order.price and order.order_type == "limit"
            else 0
        )
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "PDNO": order.ticker,
            "ORD_DVSN": "00" if order.order_type == "limit" else "01",
            "ORD_QTY": str(order.quantity),
            "ORD_UNPR": str(unpr),
        }
        try:
            r = httpx.post(url, json=body, headers=self._headers(tr_id), timeout=15)
            r.raise_for_status()
            resp = r.json()
            status = "submitted" if resp.get("rt_cd") == "0" else "rejected"
            return ExecutionResult(
                order=order,
                submitted_at=datetime.utcnow(),
                status=status,
                broker_order_id=(resp.get("output") or {}).get("ODNO"),
                message=resp.get("msg1", ""),
            )
        except Exception as e:
            log.error("kis.order_failed", error=str(e), order=order.model_dump())
            return ExecutionResult(
                order=order,
                submitted_at=datetime.utcnow(),
                status="rejected",
                message=str(e),
            )

    def get_unfilled_orders(self) -> list[dict[str, Any]]:
        """List today's open (unfilled) orders.

        Returns a normalised list of:
            ``{ticker, broker_order_id, side, ord_qty, ccld_qty, remaining,
                price, ord_type}``

        Empty list on failure / no unfilled orders.
        """
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
        cano, prdt = self._split_account()
        tr_id = "VTTC8036R" if self.env == "paper" else "TTTC8036R"
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "INQR_DVSN_1": "0",
            "INQR_DVSN_2": "0",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        try:
            r = httpx.get(
                url, params=params, headers=self._headers(tr_id), timeout=15
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning("kis.unfilled_failed", error=str(e))
            return []

        out: list[dict[str, Any]] = []
        for row in data.get("output") or []:
            try:
                ord_qty = int(float(row.get("ord_qty") or 0))
                ccld_qty = int(float(row.get("tot_ccld_qty") or 0))
                remaining = max(ord_qty - ccld_qty, 0)
                if remaining <= 0:
                    continue
                sll_buy = str(row.get("sll_buy_dvsn_cd") or "")
                # KIS code: '01' = SELL, '02' = BUY (도메스틱)
                side = "SELL" if sll_buy == "01" else "BUY"
                out.append(
                    {
                        "ticker": str(row.get("pdno") or ""),
                        "broker_order_id": str(row.get("odno") or ""),
                        "side": side,
                        "ord_qty": ord_qty,
                        "ccld_qty": ccld_qty,
                        "remaining": remaining,
                        "price": float(row.get("ord_unpr") or 0),
                        "ord_type": str(row.get("ord_dvsn_cd") or ""),
                    }
                )
            except Exception:
                continue
        return out

    def cancel_order(self, ticker: str, broker_order_id: str, qty: int) -> dict[str, Any]:
        """Cancel an open order."""
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-rvsecncl"
        cano, prdt = self._split_account()
        tr_id = "VTTC0803U" if self.env == "paper" else "TTTC0803U"
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": broker_order_id,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",  # 02 = cancel
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        try:
            r = httpx.post(url, json=body, headers=self._headers(tr_id), timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error("kis.cancel_failed", error=str(e), order_id=broker_order_id)
            return {"rt_cd": "1", "msg1": str(e)}

    def _order_tr_id(self, side: Side) -> str:
        """TR IDs differ between paper (VTT) and live (TTT) environments."""
        if self.env == "paper":
            return "VTTC0802U" if side is Side.BUY else "VTTC0801U"
        return "TTTC0802U" if side is Side.BUY else "TTTC0801U"

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------
    def get_balance(self) -> dict[str, Any]:
        """Raw balance response. Use ``get_account_state`` for the parsed view."""
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        cano, prdt = self._split_account()
        tr_id = "VTTC8434R" if self.env == "paper" else "TTTC8434R"
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        r = httpx.get(url, params=params, headers=self._headers(tr_id), timeout=15)
        r.raise_for_status()
        return r.json()

    def get_account_state(self) -> dict[str, Any]:
        """Parsed account state.

        Returns:
            ``{"positions": {ticker: qty}, "prices": {ticker: avg_buy_price},
              "cash": float, "nav": float, "raw": <full json>}``

        Falls back to an empty state on auth/network errors so the daily
        pipeline can run dry without a configured KIS account.
        """
        try:
            raw = self.get_balance()
        except Exception as e:
            log.warning("kis.balance_unavailable", error=str(e))
            return {"positions": {}, "prices": {}, "cash": 0.0, "nav": 0.0, "raw": {}}

        positions: dict[str, int] = {}
        prices: dict[str, float] = {}
        for row in raw.get("output1", []) or []:
            tkr = row.get("pdno") or ""
            qty = int(float(row.get("hldg_qty") or 0))
            if not tkr or qty <= 0:
                continue
            positions[tkr] = qty
            try:
                prices[tkr] = float(row.get("prpr") or row.get("pchs_avg_pric") or 0)
            except ValueError:
                prices[tkr] = 0.0

        summary = (raw.get("output2") or [{}])[0]
        try:
            cash = float(summary.get("dnca_tot_amt") or 0)
            nav = float(summary.get("tot_evlu_amt") or summary.get("nass_amt") or 0)
        except ValueError:
            cash, nav = 0.0, 0.0

        return {
            "positions": positions,
            "prices": prices,
            "cash": cash,
            "nav": nav,
            "raw": raw,
        }
