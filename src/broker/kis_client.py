"""Korea Investment & Securities (KIS) Open API client.

Reference: https://apiportal.koreainvestment.com/

This is a lean wrapper focused on what MAI-System needs:
- OAuth token issuance and caching
- Domestic stock quote
- Domestic stock order (cash buy/sell)
- Balance and position lookup

For the paper environment, set `KIS_ENV=paper` in `.env`. The base URL switches
automatically.
"""
from __future__ import annotations

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
        self.env = env.kis_env.lower()
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
        except Exception:  # noqa: BLE001
            pass

    def _save_cached_token(self) -> None:
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
        # KIS tokens expire after ~24h; use 23h safety margin
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
        if "-" in self.account_no:
            head, tail = self.account_no.split("-", 1)
            return head, tail
        return self.account_no[:8], self.account_no[8:] or "01"

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
    def get_price(self, ticker: str) -> float:
        """Return current price (KRW)."""
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        r = httpx.get(url, params=params, headers=self._headers("FHKST01010100"), timeout=10)
        r.raise_for_status()
        data = r.json()
        return float(data["output"]["stck_prpr"])

    # ------------------------------------------------------------------
    # Trading
    # ------------------------------------------------------------------
    def place_order(self, order: Order) -> ExecutionResult:
        tr_id = self._order_tr_id(order.side)
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        cano, prdt = self._split_account()
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "PDNO": order.ticker,
            "ORD_DVSN": "00" if order.order_type == "limit" else "01",
            "ORD_QTY": str(order.quantity),
            "ORD_UNPR": str(int(order.price or 0)),
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
                broker_order_id=resp.get("output", {}).get("ODNO"),
                message=resp.get("msg1", ""),
            )
        except Exception as e:  # noqa: BLE001
            log.error("kis.order_failed", error=str(e), order=order.model_dump())
            return ExecutionResult(
                order=order,
                submitted_at=datetime.utcnow(),
                status="rejected",
                message=str(e),
            )

    def _order_tr_id(self, side: Side) -> str:
        """TR IDs differ between paper (VTT) and live (TTT) environments."""
        if self.env == "paper":
            return "VTTC0802U" if side is Side.BUY else "VTTC0801U"
        return "TTTC0802U" if side is Side.BUY else "TTTC0801U"

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------
    def get_balance(self) -> dict[str, Any]:
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
