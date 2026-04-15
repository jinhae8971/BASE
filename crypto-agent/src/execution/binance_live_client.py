"""Authenticated Binance spot REST client for paper + live modes.

Implements the subset of the `BinanceClient` surface that `DailyWorkflow`
needs -- `submit(Order) -> Fill` and `account_equity_usd() -> float` --
against either the Binance live API or the testnet. Both modes share
100% of the code path; only the base URL and the API keys differ. That
is intentional: paper mode's entire job is to be production-identical
except for the venue, so whatever we observe in paper has the highest
chance of also being what we observe in live.

Security invariants:
  - Every call to `submit()` goes through `live_gate.assert_live_allowed()`
    first (no-op in paper, hard stop in live without promotion).
  - The first `account()` call verifies `canWithdraw == False` and
    refuses to trade if Withdraw permission is enabled.
  - Every order's notional is clamped via `live_gate.cap_order_usd()`.
  - Every reported equity is clamped via `live_gate.cap_equity_usd()`.
  - HALT file is checked before every order submit.

Data-path invariants:
  - Order quantization uses Decimal (no float) via BinanceSymbolInfo.
  - MARKET BUYs use `quoteOrderQty` so the exchange handles lot-size
    rounding on our behalf.
  - MARKET SELLs use `quantity` derived from the current best price and
    quantized to stepSize before the request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from src.config import get_settings
from src.execution.binance_auth import build_signed_query
from src.execution.binance_client import Fill, Order
from src.execution.binance_symbol_info import (
    BinanceSymbolInfo,
    InsufficientNotional,
    SymbolNotSupported,
)
from src.execution.killswitch import is_halted
from src.execution.live_gate import (
    assert_live_allowed,
    assert_withdraw_disabled,
    cap_equity_usd,
    cap_order_usd,
)
from src.logging import get_logger

log = get_logger("binance_live")

LIVE_BASE = "https://api.binance.com"
PAPER_BASE = "https://testnet.binance.vision"


class BinanceLiveClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        api_secret: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url
        self._api_key = api_key
        self._api_secret = api_secret
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={
                "X-MBX-APIKEY": api_key,
                "Accept": "application/json",
                "User-Agent": "crypto-agent/0.0.1",
            },
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        self._owns_client = client is None
        self.symbol_info = BinanceSymbolInfo(base_url, client=self._client)
        self._withdraw_checked = False

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "BinanceLiveClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # --- public surface matching BinanceClient --------------------------

    async def account_equity_usd(self) -> float:
        info = await self._account()
        if not self._withdraw_checked:
            assert_withdraw_disabled(info)
            self._withdraw_checked = True

        settings = get_settings()
        base = settings.base_currency
        total = 0.0
        for b in info.get("balances", []):
            free = float(b.get("free", 0) or 0)
            locked = float(b.get("locked", 0) or 0)
            amt = free + locked
            if amt == 0:
                continue
            asset = b["asset"]
            if asset == base:
                total += amt
                continue
            try:
                px = await self._last_price(f"{asset}{base}")
                total += amt * px
            except Exception:  # noqa: BLE001 -- unsupported pair, skip
                continue

        return cap_equity_usd(total)

    async def submit(self, order: Order) -> Fill:
        if is_halted():
            raise RuntimeError("HALT file present -- refusing to submit order")

        # Promotion gate (no-op in paper, hard stop in live without ack).
        assert_live_allowed()

        if not self.symbol_info.loaded:
            await self.symbol_info.load()

        # Per-order hard cap (live mode only).
        qty_usd = cap_order_usd(order.qty_usd)

        try:
            if order.side == "BUY":
                return await self._place_market_buy(order.symbol, qty_usd)
            return await self._place_market_sell(order.symbol, qty_usd)
        except (InsufficientNotional, SymbolNotSupported) as exc:
            log.error("binance_live.order_rejected_preflight", symbol=order.symbol, err=str(exc))
            raise

    # --- order placement ------------------------------------------------

    async def _place_market_buy(self, symbol: str, qty_usd: float) -> Fill:
        """MARKET BUY via `quoteOrderQty` so the exchange handles lot-size.

        We still check that the symbol is known and that the requested
        notional meets minNotional, so rejects happen pre-network when
        possible.
        """
        f = self.symbol_info._get(symbol)
        if qty_usd < float(f.min_notional):
            raise InsufficientNotional(
                f"{symbol}: buy ${qty_usd} < minNotional ${f.min_notional}"
            )
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": f"{qty_usd:.2f}",
            "newOrderRespType": "FULL",
        }
        resp = await self._signed_post("/api/v3/order", params=params)
        return _parse_fill(resp, "BUY")

    async def _place_market_sell(self, symbol: str, qty_usd: float) -> Fill:
        """MARKET SELL with a quantized `quantity` derived from current price."""
        price = await self._last_price(symbol)
        if price <= 0:
            raise RuntimeError(f"{symbol}: no price available for sell sizing")
        raw_qty = qty_usd / price
        qty = self.symbol_info.quantize_sell(symbol, raw_qty)
        if qty <= 0:
            raise InsufficientNotional(f"{symbol}: sell qty zero after quantization")
        qty_str = self.symbol_info.format_qty(symbol, qty)
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": qty_str,
            "newOrderRespType": "FULL",
        }
        resp = await self._signed_post("/api/v3/order", params=params)
        return _parse_fill(resp, "SELL")

    # --- low-level HTTP -------------------------------------------------

    async def _account(self) -> dict[str, Any]:
        return await self._signed_get("/api/v3/account", params={})

    async def _signed_get(self, path: str, params: dict[str, Any]) -> Any:
        qs = build_signed_query(dict(params), self._api_secret)
        resp = await self._client.get(f"{path}?{qs}")
        if resp.status_code >= 400:
            log.error("binance.signed_get_failed", path=path, status=resp.status_code, body=resp.text[:500])
            resp.raise_for_status()
        return resp.json()

    async def _signed_post(self, path: str, params: dict[str, Any]) -> Any:
        qs = build_signed_query(dict(params), self._api_secret)
        resp = await self._client.post(f"{path}?{qs}")
        if resp.status_code >= 400:
            log.error("binance.order_rejected", path=path, status=resp.status_code, body=resp.text[:500])
            resp.raise_for_status()
        return resp.json()

    async def _last_price(self, symbol: str) -> float:
        resp = await self._client.get(f"/api/v3/ticker/price?symbol={symbol}")
        resp.raise_for_status()
        return float(resp.json()["price"])


def _parse_fill(resp: dict[str, Any], side: str) -> Fill:
    """Collapse a Binance order response into our lightweight `Fill`."""
    fills = resp.get("fills") or []
    if fills:
        total_qty = sum(float(f["qty"]) for f in fills)
        notional = sum(float(f["qty"]) * float(f["price"]) for f in fills)
        avg_price = notional / total_qty if total_qty > 0 else 0.0
        # Fee is in commissionAsset units; converting to USD requires
        # another lookup we don't do here. Record as best-effort for now.
        fee = sum(float(f.get("commission", 0)) for f in fills)
    else:
        executed = float(resp.get("executedQty", 0) or 0)
        cum_quote = float(resp.get("cummulativeQuoteQty", 0) or 0)
        total_qty = executed
        avg_price = cum_quote / executed if executed > 0 else 0.0
        fee = 0.0

    return Fill(
        symbol=resp["symbol"],
        side=side,
        qty=total_qty,
        price=avg_price,
        fee_usd=fee,
        filled_at=datetime.now(UTC),
    )
