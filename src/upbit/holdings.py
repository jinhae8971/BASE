"""장기보유 코인 제외 — the user's buy-and-hold bag is off-limits to the engine.

Two guarantees, enforced on both sides of every cycle:

1. **Never bought.** Long-term symbols are dropped from the tradable universe,
   so the morning scan cannot select them no matter how well they score.
2. **Never sold.** Sizing and liquidation subtract the locked quantity from the
   balance, so a panic-sell or an EOD flatten can only ever touch what the
   engine itself accumulated.

``locked_quantity == 0`` means *the entire balance of this coin is protected*
(the common case: "BTC는 손대지 마"). A positive value protects that many units
and leaves any excess tradable.
"""
from __future__ import annotations

from typing import Any

from common.logging import get_logger

from .store import UpbitStore, get_store
from .types import LongTermHolding

log = get_logger(__name__)


class HoldingsGuard:
    """Read-through view of the exclusion list, cached for one cycle."""

    def __init__(self, holdings: list[LongTermHolding]) -> None:
        self._by_symbol = {h.symbol.upper(): h for h in holdings}

    @classmethod
    def load(cls, store: UpbitStore | None = None) -> HoldingsGuard:
        return cls((store or get_store()).list_long_term())

    # ------------------------------------------------------------------
    @property
    def symbols(self) -> set[str]:
        return set(self._by_symbol)

    def is_protected(self, symbol_or_market: str) -> bool:
        """True when the engine must not open a new position in this coin."""
        return _symbol(symbol_or_market) in self._by_symbol

    def locked_quantity(self, symbol_or_market: str) -> float:
        """Units the engine may not sell. ``inf`` when the whole bag is locked."""
        holding = self._by_symbol.get(_symbol(symbol_or_market))
        if holding is None:
            return 0.0
        return float("inf") if holding.locked_quantity <= 0 else holding.locked_quantity

    def tradable_quantity(self, symbol_or_market: str, balance: float) -> float:
        """How much of an on-exchange balance the engine is allowed to sell."""
        locked = self.locked_quantity(symbol_or_market)
        if locked == float("inf"):
            return 0.0
        return max(balance - locked, 0.0)

    def filter_markets(self, markets: list[str]) -> list[str]:
        return [m for m in markets if not self.is_protected(m)]

    def split_balances(
        self, accounts: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Partition Upbit `/v1/accounts` rows into (tradable, long-term)."""
        tradable: list[dict[str, Any]] = []
        long_term: list[dict[str, Any]] = []
        for acc in accounts:
            currency = str(acc.get("currency", "")).upper()
            if currency == "KRW":
                tradable.append(acc)
                continue
            balance = float(acc.get("balance") or 0) + float(acc.get("locked") or 0)
            if not self.is_protected(currency):
                tradable.append(acc)
                continue
            free = self.tradable_quantity(currency, balance)
            locked = balance - free
            long_term.append({**acc, "_protected_quantity": locked})
            if free > 0:
                tradable.append({**acc, "balance": free, "locked": 0})
        return tradable, long_term


def _symbol(value: str) -> str:
    """Accept ``"KRW-BTC"`` or ``"BTC"`` interchangeably."""
    return value.split("-")[-1].strip().upper()


# ----------------------------------------------------------------------
# CRUD used by the dashboard's 장기보유 tab
# ----------------------------------------------------------------------
def list_holdings(store: UpbitStore | None = None) -> list[LongTermHolding]:
    return (store or get_store()).list_long_term()


def add_holding(
    symbol: str,
    locked_quantity: float = 0.0,
    memo: str = "",
    store: UpbitStore | None = None,
) -> LongTermHolding:
    symbol = _symbol(symbol)
    if not symbol:
        raise ValueError("심볼을 입력하세요 (예: BTC).")
    if symbol == "KRW":
        raise ValueError("KRW 는 장기보유 목록에 등록할 수 없습니다.")
    if locked_quantity < 0:
        raise ValueError("잠금 수량은 0 이상이어야 합니다.")

    store = store or get_store()
    holding = LongTermHolding(symbol=symbol, locked_quantity=locked_quantity, memo=memo)
    store.upsert_long_term(holding)
    store.log_event(
        "info",
        "holdings",
        f"장기보유 등록: {symbol}"
        + (" (전량 보호)" if locked_quantity <= 0 else f" ({locked_quantity} 개 보호)"),
    )
    log.info("upbit.holdings.added", symbol=symbol, locked=locked_quantity)
    return holding


def remove_holding(symbol: str, store: UpbitStore | None = None) -> bool:
    store = store or get_store()
    removed = store.delete_long_term(_symbol(symbol))
    if removed:
        store.log_event("info", "holdings", f"장기보유 해제: {_symbol(symbol)}")
        log.info("upbit.holdings.removed", symbol=_symbol(symbol))
    return removed
