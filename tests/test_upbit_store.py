"""SQLite persistence — everything the dashboard reads back."""
from __future__ import annotations

import pytest

from upbit.store import UpbitStore, utc_now
from upbit.types import Candidate, Position, ScoreBreakdown


@pytest.fixture
def store(tmp_path) -> UpbitStore:
    return UpbitStore(tmp_path / "upbit.sqlite")


def _candidate(market: str, total: float, selected: bool = False) -> Candidate:
    return Candidate(
        market=market,
        symbol=market.split("-")[-1],
        korean_name=market.split("-")[-1] + "코인",
        price=1000.0,
        trade_price_24h=2.0e10,
        rank=1,
        selected=selected,
        reason="테스트",
        score=ScoreBreakdown(
            volume=70, flow=60, technical=80, beta=50, total=total, metrics={"rsi_14": 61.2}
        ),
    )


def test_run_lifecycle(store: UpbitStore) -> None:
    run_id = store.start_run("selection", "paper", "테스트")
    store.finish_run(run_id, status="ok", regime="risk_on", scanned=40, selected=3)
    run = store.get_run(run_id)
    assert run["status"] == "ok" and run["regime"] == "risk_on"
    assert run["scanned"] == 40 and run["selected"] == 3
    assert run["finished_at"]
    assert store.latest_analysis_run_id() == run_id


def test_analyses_round_trip_with_metrics(store: UpbitStore) -> None:
    run_id = store.start_run("selection", "paper")
    store.save_analyses(
        run_id, "2026-08-22", [_candidate("KRW-XRP", 78.5, True), _candidate("KRW-DOGE", 61.0)]
    )
    items = store.list_analyses(run_id=run_id)
    assert len(items) == 2
    assert items[0]["total_score"] == pytest.approx(78.5)   # ordered by score
    assert items[0]["selected"] == 1
    assert items[0]["metrics"]["rsi_14"] == pytest.approx(61.2)

    assert len(store.list_analyses(run_id=run_id, selected_only=True)) == 1
    assert len(store.list_analyses(market="KRW-DOGE")) == 1


def test_trade_stats_summarises_closed_trades(store: UpbitStore) -> None:
    common = {"mode": "paper", "ord_type": "market", "state": "done"}
    store.record_trade(market="KRW-A", symbol="A", side="bid", krw_amount=100000, fee=50, **common)
    store.record_trade(
        market="KRW-A", symbol="A", side="ask", krw_amount=110000, fee=55,
        pnl=10000, pnl_pct=0.10, **common,
    )
    store.record_trade(
        market="KRW-B", symbol="B", side="ask", krw_amount=95000, fee=47,
        pnl=-5000, pnl_pct=-0.05, **common,
    )

    stats = store.trade_stats(mode="paper")
    assert stats["closed_trades"] == 2
    assert stats["wins"] == 1 and stats["losses"] == 1
    assert stats["win_rate"] == pytest.approx(0.5)
    assert stats["total_pnl"] == pytest.approx(5000)
    assert stats["profit_factor"] == pytest.approx(2.0)
    assert stats["total_fee"] == pytest.approx(152)
    assert stats["best"]["pnl"] == pytest.approx(10000)


def test_trade_stats_is_empty_safe(store: UpbitStore) -> None:
    stats = store.trade_stats(mode="paper")
    assert stats["closed_trades"] == 0
    assert stats["win_rate"] == 0.0
    assert stats["profit_factor"] is None


def test_position_lifecycle(store: UpbitStore) -> None:
    pos = Position(
        market="KRW-XRP", symbol="XRP", volume=100.0, avg_price=900.0,
        opened_at=utc_now(), high_water=910.0, stop_price=873.0,
        take_price=945.0, mode="paper",
    )
    pid = store.open_position(pos, entry_trade_id=1, score=71.4)
    assert len(store.list_open_positions("paper")) == 1

    store.update_position(pid, high_water=980.0)
    assert store.list_open_positions("paper")[0]["high_water"] == pytest.approx(980.0)

    store.close_position(pid, exit_reason="take_profit", realized_pnl=4200.0)
    assert store.list_open_positions("paper") == []
    closed = store.list_closed_positions(mode="paper")
    assert closed[0]["exit_reason"] == "take_profit"
    assert closed[0]["realized_pnl"] == pytest.approx(4200.0)
    assert closed[0]["entry_score"] == pytest.approx(71.4)


def test_settings_overrides_round_trip(store: UpbitStore) -> None:
    store.set_override("config", {"strategy": {"max_positions": 8}})
    assert store.get_override("config")["strategy"]["max_positions"] == 8
    store.set_override("config", {"strategy": {"max_positions": 3}})
    assert store.get_override("config")["strategy"]["max_positions"] == 3
    assert "config" in store.all_overrides()
    store.delete_override("config")
    assert store.get_override("config", {}) == {}


def test_equity_history_and_last(store: UpbitStore) -> None:
    store.snapshot_equity(mode="paper", total_krw=10_000_000, cash_krw=8_000_000, trading_krw=2_000_000)
    store.snapshot_equity(mode="paper", total_krw=10_500_000, cash_krw=7_000_000, trading_krw=3_500_000)
    history = store.equity_history(days=7, mode="paper")
    assert len(history) == 2
    assert store.last_equity("paper")["total_krw"] == pytest.approx(10_500_000)


def test_event_log_filters_by_level(store: UpbitStore) -> None:
    store.log_event("info", "order", "매수 체결")
    store.log_event("error", "order", "매도 실패")
    assert len(store.list_events()) == 2
    errors = store.list_events(level="error")
    assert len(errors) == 1 and errors[0]["message"] == "매도 실패"
