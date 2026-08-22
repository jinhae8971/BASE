"""End-to-end engine behaviour against a paper broker and a scripted market.

No network, no real orders: the fake client serves deterministic candles and
the PaperBroker keeps its ledger in a temp SQLite file.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from upbit_fakes import FakeUpbitClient

from upbit.broker import PaperBroker
from upbit.engine import KST, TradingEngine
from upbit.holdings import add_holding
from upbit.risk import EquityView, RiskGuard
from upbit.store import UpbitStore
from upbit.strategy import UpbitConfig
from upbit.types import ExitReason, MarketRegimeView, Regime

MARKETS = ["KRW-BTC", "KRW-XRP", "KRW-DOGE", "KRW-SOL", "KRW-ADA", "KRW-USDT"]


@pytest.fixture
def store(tmp_path) -> UpbitStore:
    return UpbitStore(tmp_path / "upbit.sqlite")


@pytest.fixture
def client() -> FakeUpbitClient:
    # BTC trends up (risk-on), the alts trend up harder — a textbook alt season.
    return FakeUpbitClient(
        MARKETS,
        profiles={
            "KRW-BTC": {"drift": 0.008, "wobble": 0.004},
            "KRW-XRP": {"drift": 0.016, "wobble": 0.010, "volume_spike_last": 3.0},
            "KRW-DOGE": {"drift": 0.014, "wobble": 0.012, "volume_spike_last": 2.5},
            "KRW-SOL": {"drift": 0.011, "wobble": 0.008},
            "KRW-ADA": {"drift": -0.006, "wobble": 0.004},
            "KRW-USDT": {"drift": 0.0, "wobble": 0.0001},
        },
        prices={
            "KRW-BTC": 90_000_000, "KRW-XRP": 1_500, "KRW-DOGE": 300,
            "KRW-SOL": 250_000, "KRW-ADA": 800, "KRW-USDT": 1_380,
        },
    )


def build_engine(store: UpbitStore, client: FakeUpbitClient, **overrides) -> TradingEngine:
    config = UpbitConfig.model_validate(
        {
            "mode": "paper",
            "paper_initial_krw": 10_000_000,
            "strategy": {"max_positions": 3, "min_score": 40.0, "position_pct": 0.2},
            "universe": {"min_trade_price_24h": 1_000_000_000, "max_candidates": 20},
            **overrides,
        }
    )
    broker = PaperBroker(client, store, fee_rate=config.fee_rate, initial_krw=config.paper_initial_krw)
    return TradingEngine(config=config, store=store, client=client, broker=broker)


# ----------------------------------------------------------------------
# Selection
# ----------------------------------------------------------------------
def test_dry_run_scores_but_never_orders(store: UpbitStore, client: FakeUpbitClient) -> None:
    engine = build_engine(store, client)
    result = engine.run_selection(dry_run=True)

    assert "error" not in result
    assert result["scanned"] > 0
    assert store.list_analyses(run_id=result["run_id"])
    assert store.list_open_positions("paper") == []
    assert store.list_trades(mode="paper") == []
    assert all(d["action"] != "buy" for d in result["decisions"])


def test_live_paper_run_opens_positions(store: UpbitStore, client: FakeUpbitClient) -> None:
    engine = build_engine(store, client)
    result = engine.run_selection(dry_run=False)

    positions = store.list_open_positions("paper")
    assert 0 < len(positions) <= 3
    assert len(result["selected"]) == len(positions)

    buys = store.list_trades(mode="paper", side="bid")
    assert len(buys) == len(positions)
    for pos in positions:
        assert pos["avg_price"] > 0
        assert pos["stop_price"] < pos["avg_price"] < pos["take_price"]
        assert pos["entry_score"] >= 40.0


def test_stablecoins_and_low_liquidity_are_screened_out(
    store: UpbitStore, client: FakeUpbitClient
) -> None:
    engine = build_engine(store, client)
    result = engine.run_selection(dry_run=True)
    scanned = {a["market"] for a in store.list_analyses(run_id=result["run_id"])}
    assert "KRW-USDT" not in scanned


def test_warned_markets_are_excluded(store: UpbitStore) -> None:
    client = FakeUpbitClient(
        MARKETS,
        profiles={m: {"drift": 0.01} for m in MARKETS},
        prices=dict.fromkeys(MARKETS, 1000.0),
        warnings={"KRW-DOGE"},
    )
    engine = build_engine(store, client)
    result = engine.run_selection(dry_run=True)
    scanned = {a["market"] for a in store.list_analyses(run_id=result["run_id"])}
    assert "KRW-DOGE" not in scanned


def test_risk_off_regime_blocks_all_entries(store: UpbitStore) -> None:
    bearish = FakeUpbitClient(
        MARKETS,
        profiles={m: {"drift": -0.012, "wobble": 0.004} for m in MARKETS},
        prices=dict.fromkeys(MARKETS, 1000.0),
    )
    engine = build_engine(store, bearish)
    result = engine.run_selection(dry_run=False)

    assert result["regime"]["regime"] == Regime.RISK_OFF.value
    assert store.list_open_positions("paper") == []
    assert all(d["action"] != "buy" for d in result["decisions"])


# ----------------------------------------------------------------------
# 장기보유 제외 — the headline requirement
# ----------------------------------------------------------------------
def test_long_term_coins_are_never_selected(store: UpbitStore, client: FakeUpbitClient) -> None:
    add_holding("XRP", 0, "장기 보유", store=store)
    add_holding("DOGE", 0, store=store)

    engine = build_engine(store, client)
    result = engine.run_selection(dry_run=False)

    scanned = {a["market"] for a in store.list_analyses(run_id=result["run_id"])}
    assert "KRW-XRP" not in scanned and "KRW-DOGE" not in scanned
    assert {p["symbol"] for p in store.list_open_positions("paper")}.isdisjoint({"XRP", "DOGE"})
    assert {t["symbol"] for t in store.list_trades(mode="paper")}.isdisjoint({"XRP", "DOGE"})


def test_long_term_balance_survives_a_liquidation(
    store: UpbitStore, client: FakeUpbitClient
) -> None:
    engine = build_engine(store, client)
    engine.run_selection(dry_run=False)
    opened = store.list_open_positions("paper")
    assert opened, "테스트 전제: 포지션이 열려 있어야 한다"

    # The user buys BTC long-term outside the engine, then protects it.
    engine.broker.deposit(5_000_000)
    engine.broker.buy_market("KRW-BTC", 4_000_000, reason="장기 매수")
    btc_before = engine.broker.coin_balance("BTC")
    assert btc_before > 0
    add_holding("BTC", 0, "장기 보유", store=store)

    result = engine.liquidate_all(reason="panic")

    assert "BTC" in result["protected_symbols"]
    assert engine.broker.coin_balance("BTC") == pytest.approx(btc_before)
    assert store.list_open_positions("paper") == []

    # The BTC day-trade position is released, not sold — no ask trade for it.
    released = [c for c in result["closed"] if c["market"] == "KRW-BTC"]
    assert released and released[0]["reason"] == ExitReason.LONG_TERM_PROTECTED.value
    assert released[0]["released"] is True
    assert "BTC" not in {t["symbol"] for t in store.list_trades(mode="paper", side="ask")}


def test_partial_lock_only_sells_the_excess(store: UpbitStore, client: FakeUpbitClient) -> None:
    engine = build_engine(store, client)
    engine.broker.buy_market("KRW-SOL", 2_000_000, reason="시드")
    held = engine.broker.coin_balance("SOL")
    locked = held * 0.6
    add_holding("SOL", locked, "일부만 장기 보유", store=store)
    engine.reload()

    assert engine.guard.tradable_quantity("SOL", held) == pytest.approx(held - locked)
    assert engine.guard.is_protected("SOL")  # still barred from new entries


# ----------------------------------------------------------------------
# Exits
# ----------------------------------------------------------------------
def test_take_profit_closes_the_position(store: UpbitStore, client: FakeUpbitClient) -> None:
    engine = build_engine(store, client)
    engine.run_selection(dry_run=False)
    positions = store.list_open_positions("paper")
    assert positions

    # Gap every held market up past its take-profit level.
    for pos in positions:
        client.prices[pos["market"]] = float(pos["take_price"]) * 1.05

    result = engine.monitor_positions()

    assert len(result["closed"]) == len(positions)
    assert all(c["reason"] == ExitReason.TAKE_PROFIT.value for c in result["closed"])
    assert store.list_open_positions("paper") == []
    closed = store.list_closed_positions(mode="paper")
    assert all(c["realized_pnl"] > 0 for c in closed)


def test_stop_loss_closes_the_position(store: UpbitStore, client: FakeUpbitClient) -> None:
    engine = build_engine(store, client)
    engine.run_selection(dry_run=False)
    positions = store.list_open_positions("paper")
    for pos in positions:
        client.prices[pos["market"]] = float(pos["stop_price"]) * 0.95

    result = engine.monitor_positions()
    assert all(c["reason"] == ExitReason.STOP_LOSS.value for c in result["closed"])
    assert all(c["pnl"] < 0 for c in result["closed"])


def test_time_exit_after_max_hold_hours(store: UpbitStore, client: FakeUpbitClient) -> None:
    engine = build_engine(store, client, strategy={"max_positions": 3, "min_score": 40.0,
                                                   "position_pct": 0.2, "max_hold_hours": 6})
    engine.run_selection(dry_run=False)
    stale = (datetime.now(KST) - timedelta(hours=9)).isoformat(timespec="seconds")
    for pos in store.list_open_positions("paper"):
        store.update_position(pos["id"], opened_at=stale)

    result = engine.monitor_positions()
    assert result["closed"]
    assert all(c["reason"] == ExitReason.TIME_EXIT.value for c in result["closed"])


def test_force_exit_flattens_everything(store: UpbitStore, client: FakeUpbitClient) -> None:
    engine = build_engine(store, client)
    engine.run_selection(dry_run=False)
    n = len(store.list_open_positions("paper"))
    assert n > 0

    result = engine.monitor_positions(force_exit=True)
    assert len(result["closed"]) == n
    assert store.list_open_positions("paper") == []


def test_trailing_stop_arms_only_after_the_trigger(store: UpbitStore) -> None:
    config = UpbitConfig.model_validate(
        {"strategy": {"trailing_activate_pct": 0.03, "trailing_gap_pct": 0.02}}
    )
    guard = RiskGuard(config, store)
    assert guard.trailing_stop(1000.0, 1020.0) is None            # +2%: not yet armed
    assert guard.trailing_stop(1000.0, 1050.0) == pytest.approx(1029.0)  # 1050 * 0.98


def test_high_water_mark_ratchets_up(store: UpbitStore, client: FakeUpbitClient) -> None:
    engine = build_engine(store, client)
    engine.run_selection(dry_run=False)
    pos = store.list_open_positions("paper")[0]
    client.prices[pos["market"]] = float(pos["avg_price"]) * 1.02  # below take-profit

    engine.monitor_positions()
    updated = next(p for p in store.list_open_positions("paper") if p["id"] == pos["id"])
    assert updated["high_water"] >= float(pos["avg_price"]) * 1.02


# ----------------------------------------------------------------------
# Accounting
# ----------------------------------------------------------------------
def test_round_trip_pnl_reflects_fees(store: UpbitStore, client: FakeUpbitClient) -> None:
    engine = build_engine(store, client)
    engine.run_selection(dry_run=False)
    pos = store.list_open_positions("paper")[0]

    # Sell back at exactly the entry price: both fees plus slippage must show up.
    client.prices[pos["market"]] = float(pos["avg_price"])
    engine.monitor_positions(force_exit=True)

    sells = store.list_trades(mode="paper", side="ask")
    assert sells and sells[0]["pnl"] < 0


def test_equity_view_separates_the_protected_bag(
    store: UpbitStore, client: FakeUpbitClient
) -> None:
    engine = build_engine(store, client)
    engine.broker.buy_market("KRW-BTC", 3_000_000, reason="장기 매수")
    add_holding("BTC", 0, store=store)
    engine.reload()

    equity = engine.equity_view()
    assert equity.longterm_krw if False else equity.longterm_value_krw > 0
    assert equity.tradable_equity == pytest.approx(equity.cash_krw + equity.trading_value_krw)
    assert equity.total_equity > equity.tradable_equity


def test_snapshot_writes_the_equity_curve(store: UpbitStore, client: FakeUpbitClient) -> None:
    engine = build_engine(store, client)
    snapshot = engine.snapshot_equity()
    assert snapshot["total_krw"] > 0
    assert len(store.equity_history(days=1, mode="paper")) == 1


def test_daily_loss_kill_halts_new_entries(store: UpbitStore, client: FakeUpbitClient) -> None:
    config = UpbitConfig.model_validate({"risk": {"daily_loss_kill_pct": 0.01}})
    guard = RiskGuard(config, store)
    store.record_trade(
        mode="paper", market="KRW-X", symbol="X", side="ask", ord_type="market",
        state="done", volume=1, price=1, krw_amount=1, fee=0, pnl=-500_000, pnl_pct=-0.1,
    )
    equity = EquityView(cash_krw=9_000_000, trading_value_krw=1_000_000)
    halted, loss_pct = guard.daily_loss_breached(equity)
    assert halted and loss_pct == pytest.approx(0.05)


def test_available_capital_respects_the_cash_buffer(store: UpbitStore) -> None:
    config = UpbitConfig.model_validate(
        {"risk": {"max_total_exposure_pct": 0.8, "min_cash_buffer_pct": 0.2}}
    )
    guard = RiskGuard(config, store)
    equity = EquityView(cash_krw=10_000_000, trading_value_krw=0)
    regime = MarketRegimeView(regime=Regime.RISK_ON, exposure_multiplier=1.0)
    # 80% exposure cap vs. keeping 20% in cash -> the buffer binds first.
    assert guard.available_capital(equity, regime) == pytest.approx(8_000_000)


def test_sizing_rejects_scores_below_the_floor(store: UpbitStore) -> None:
    config = UpbitConfig.model_validate({"strategy": {"min_score": 70.0}})
    guard = RiskGuard(config, store)
    decision = guard.size_position(
        equity=EquityView(cash_krw=10_000_000),
        regime=MarketRegimeView(regime=Regime.RISK_ON, exposure_multiplier=1.0),
        score=65.0,
        open_positions=0,
        remaining_capital=10_000_000,
    )
    assert not decision.approved and "종합점수" in decision.reason
