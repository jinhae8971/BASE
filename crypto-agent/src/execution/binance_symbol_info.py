"""Exchange-info loader with lot-size and min-notional quantization.

Loads `/api/v3/exchangeInfo` once (cached) and exposes helpers that
convert a requested `qty_usd` into a valid `quantity` that respects:

  - `LOT_SIZE.stepSize`  : qty must be a multiple of stepSize
  - `LOT_SIZE.minQty`    : qty must be at least minQty
  - `NOTIONAL.minNotional`: qty * price must be at least minNotional

Orders that cannot be quantized up into a valid size (e.g. $5 worth of
BTC when minNotional is $10) are rejected at this layer BEFORE any
network I/O so the executor sees a clean failure mode.

All math uses `decimal.Decimal` — never float arithmetic on exchange
filters. Binance rejects a few bps of rounding error, and float
multiplication of arbitrary tick sizes can silently drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any

import httpx

from src.data.http import build_client, get_json
from src.logging import get_logger

log = get_logger("symbol_info")


@dataclass
class SymbolFilter:
    symbol: str
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    tick_size: Decimal


class InsufficientNotional(ValueError):
    pass


class SymbolNotSupported(ValueError):
    pass


class BinanceSymbolInfo:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url
        self._client = client or build_client(base_url=base_url)
        self._owns_client = client is None
        self._filters: dict[str, SymbolFilter] | None = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def load(self) -> None:
        data = await get_json(self._client, "/api/v3/exchangeInfo")
        filters: dict[str, SymbolFilter] = {}
        for s in data.get("symbols", []):
            sym = s["symbol"]
            step = min_qty = min_notional = tick = None
            for f in s.get("filters", []):
                t = f.get("filterType")
                if t == "LOT_SIZE":
                    step = Decimal(str(f["stepSize"]))
                    min_qty = Decimal(str(f["minQty"]))
                elif t in ("NOTIONAL", "MIN_NOTIONAL"):
                    val = f.get("minNotional") or f.get("notional") or "0"
                    min_notional = Decimal(str(val))
                elif t == "PRICE_FILTER":
                    tick = Decimal(str(f["tickSize"]))
            if step is None or min_qty is None:
                continue
            filters[sym] = SymbolFilter(
                symbol=sym,
                step_size=step,
                min_qty=min_qty,
                min_notional=min_notional or Decimal("0"),
                tick_size=tick or Decimal("0"),
            )
        self._filters = filters
        log.info("symbol_info.loaded", count=len(filters))

    @property
    def loaded(self) -> bool:
        return self._filters is not None

    def quantize_buy(self, symbol: str, qty_usd: float, price: float) -> float:
        """Return a quantity that respects stepSize + minQty + minNotional."""
        f = self._get(symbol)
        if price <= 0:
            raise ValueError(f"invalid price {price}")
        raw = Decimal(str(qty_usd)) / Decimal(str(price))
        qty = _floor_to_step(raw, f.step_size)
        if qty < f.min_qty:
            qty = f.min_qty  # round up to minQty if we're below it
        notional = qty * Decimal(str(price))
        if notional < f.min_notional:
            raise InsufficientNotional(
                f"{symbol}: notional ${notional} < min ${f.min_notional}"
            )
        return float(qty)

    def quantize_sell(self, symbol: str, qty: float) -> float:
        f = self._get(symbol)
        q = _floor_to_step(Decimal(str(qty)), f.step_size)
        if q < f.min_qty:
            raise InsufficientNotional(
                f"{symbol}: sell qty {q} below minQty {f.min_qty}"
            )
        return float(q)

    def format_qty(self, symbol: str, qty: float) -> str:
        """Format qty as a string with step-size precision (no exponent)."""
        f = self._get(symbol)
        # Decimals preserve exact representation; strip trailing zeros.
        q = _floor_to_step(Decimal(str(qty)), f.step_size)
        return format(q.normalize(), "f")

    def _get(self, symbol: str) -> SymbolFilter:
        if self._filters is None:
            raise RuntimeError("BinanceSymbolInfo.load() must be called first")
        f = self._filters.get(symbol)
        if f is None:
            raise SymbolNotSupported(f"{symbol} not in exchange info")
        return f


def _floor_to_step(qty: Decimal, step: Decimal) -> Decimal:
    if step == 0:
        return qty
    return (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
