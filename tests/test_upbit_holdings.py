"""장기보유 제외 — the guarantee that the user's long-term bag is untouchable."""
from __future__ import annotations

import pytest

from upbit.holdings import HoldingsGuard, add_holding, list_holdings, remove_holding
from upbit.store import UpbitStore
from upbit.types import LongTermHolding


@pytest.fixture
def store(tmp_path) -> UpbitStore:
    return UpbitStore(tmp_path / "upbit.sqlite")


def test_guard_protects_registered_symbols() -> None:
    guard = HoldingsGuard([LongTermHolding(symbol="BTC"), LongTermHolding(symbol="ETH")])
    assert guard.is_protected("BTC")
    assert guard.is_protected("KRW-ETH")      # market code or bare symbol
    assert not guard.is_protected("KRW-XRP")
    assert guard.symbols == {"BTC", "ETH"}


def test_zero_locked_quantity_means_the_whole_bag() -> None:
    guard = HoldingsGuard([LongTermHolding(symbol="BTC", locked_quantity=0)])
    assert guard.locked_quantity("BTC") == float("inf")
    assert guard.tradable_quantity("BTC", 3.5) == 0.0


def test_partial_lock_leaves_the_excess_tradable() -> None:
    guard = HoldingsGuard([LongTermHolding(symbol="SOL", locked_quantity=10)])
    assert guard.tradable_quantity("SOL", 25) == pytest.approx(15)
    assert guard.tradable_quantity("SOL", 4) == 0.0   # never goes negative


def test_filter_markets_drops_protected_coins() -> None:
    guard = HoldingsGuard([LongTermHolding(symbol="BTC")])
    kept = guard.filter_markets(["KRW-BTC", "KRW-XRP", "KRW-DOGE"])
    assert kept == ["KRW-XRP", "KRW-DOGE"]


def test_split_balances_partitions_the_account() -> None:
    guard = HoldingsGuard(
        [LongTermHolding(symbol="BTC"), LongTermHolding(symbol="SOL", locked_quantity=10)]
    )
    accounts = [
        {"currency": "KRW", "balance": "1000000", "locked": "0"},
        {"currency": "BTC", "balance": "0.5", "locked": "0"},
        {"currency": "SOL", "balance": "25", "locked": "0"},
        {"currency": "XRP", "balance": "500", "locked": "0"},
    ]
    tradable, long_term = guard.split_balances(accounts)

    tradable_by_currency = {a["currency"]: a for a in tradable}
    assert "BTC" not in tradable_by_currency          # fully protected
    assert float(tradable_by_currency["SOL"]["balance"]) == pytest.approx(15)
    assert float(tradable_by_currency["XRP"]["balance"]) == pytest.approx(500)
    assert float(tradable_by_currency["KRW"]["balance"]) == pytest.approx(1000000)

    protected = {a["currency"]: a["_protected_quantity"] for a in long_term}
    assert protected["BTC"] == pytest.approx(0.5)
    assert protected["SOL"] == pytest.approx(10)


def test_split_balances_counts_locked_units_as_held() -> None:
    guard = HoldingsGuard([LongTermHolding(symbol="BTC")])
    _, long_term = guard.split_balances(
        [{"currency": "BTC", "balance": "0.4", "locked": "0.1"}]
    )
    assert long_term[0]["_protected_quantity"] == pytest.approx(0.5)


def test_crud_round_trip(store: UpbitStore) -> None:
    add_holding("btc", 0, "장투용", store=store)
    add_holding("KRW-ETH", 2.5, store=store)

    symbols = {h.symbol for h in list_holdings(store)}
    assert symbols == {"BTC", "ETH"}

    assert remove_holding("BTC", store=store) is True
    assert remove_holding("BTC", store=store) is False
    assert {h.symbol for h in list_holdings(store)} == {"ETH"}


def test_add_holding_rejects_bad_input(store: UpbitStore) -> None:
    with pytest.raises(ValueError):
        add_holding("KRW", store=store)
    with pytest.raises(ValueError):
        add_holding("BTC", -1, store=store)
    with pytest.raises(ValueError):
        add_holding("  ", store=store)


def test_re_adding_updates_the_lock(store: UpbitStore) -> None:
    add_holding("BTC", 1.0, store=store)
    add_holding("BTC", 2.0, "수정", store=store)
    holdings = list_holdings(store)
    assert len(holdings) == 1
    assert holdings[0].locked_quantity == pytest.approx(2.0)
    assert holdings[0].memo == "수정"
