"""Unattended-operation properties: serialisation, reconciliation, liveness, upkeep.

These cover the failure modes that only appear after the system has been running
for a while — the ones nobody is watching for at 04:30.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pytest
from upbit_fakes import FakeUpbitClient

from upbit.broker import PaperBroker
from upbit.engine import KST, TradingEngine
from upbit.holdings import add_holding
from upbit.scheduler import STALE_INTERVAL_MULTIPLIER, UpbitScheduler
from upbit.store import UpbitStore, utc_now
from upbit.strategy import UpbitConfig
from upbit.types import Candidate, ScoreBreakdown

MARKETS = ["KRW-BTC", "KRW-XRP", "KRW-DOGE", "KRW-SOL", "KRW-ADA"]


@pytest.fixture
def store(tmp_path) -> UpbitStore:
    return UpbitStore(tmp_path / "upbit.sqlite")


@pytest.fixture
def client() -> FakeUpbitClient:
    return FakeUpbitClient(
        MARKETS,
        profiles={m: {"drift": 0.012, "wobble": 0.006} for m in MARKETS},
        prices={"KRW-BTC": 90_000_000, "KRW-XRP": 1500, "KRW-DOGE": 300,
                "KRW-SOL": 250_000, "KRW-ADA": 800},
    )


def build(store: UpbitStore, client: FakeUpbitClient, **overrides) -> TradingEngine:
    config = UpbitConfig.model_validate({
        "mode": "paper",
        "paper_initial_krw": 10_000_000,
        "strategy": {"max_positions": 3, "min_score": 40.0, "position_pct": 0.2},
        "universe": {"min_trade_price_24h": 1_000_000_000, "max_candidates": 20},
        **overrides,
    })
    broker = PaperBroker(client, store, initial_krw=config.paper_initial_krw)
    return TradingEngine(config=config, store=store, client=client, broker=broker)


# ----------------------------------------------------------------------
# Cycle serialisation
# ----------------------------------------------------------------------
def test_cycles_never_overlap(store: UpbitStore, client: FakeUpbitClient) -> None:
    """The scheduler caps instances *per job*, so selection and monitor would
    otherwise run together on one engine — reload() swapping the broker mid-scan
    while the monitor closes positions selection is still sizing against."""
    engine = build(store, client)

    original = client.get_day_candles
    client.get_day_candles = lambda m, count=200: (time.sleep(0.03), original(m, count))[1]

    marks: list[str] = []
    lock = threading.Lock()

    def instrument(name, fn):
        def wrapped(**kw):
            with lock:
                marks.append(f"{name}:in")
            try:
                return fn(**kw)
            finally:
                with lock:
                    marks.append(f"{name}:out")
        return wrapped

    engine._run_selection = instrument("selection", engine._run_selection)
    engine._monitor_positions = instrument("monitor", engine._monitor_positions)

    threads = [
        threading.Thread(target=lambda: engine.run_selection(dry_run=False)),
        threading.Thread(target=engine.monitor_positions),
    ]
    threads[0].start()
    time.sleep(0.08)
    threads[1].start()
    for t in threads:
        t.join(timeout=60)

    depth = 0
    for mark in marks:
        depth += 1 if mark.endswith(":in") else -1
        assert depth <= 1, f"사이클이 겹쳤습니다: {marks}"
    assert len(marks) == 4


def test_manual_close_is_serialised_too(store: UpbitStore, client: FakeUpbitClient) -> None:
    engine = build(store, client)
    engine.run_selection(dry_run=False)
    pos = store.list_open_positions("paper")[0]

    # Holding the cycle lock must make a concurrent manual close wait, not race.
    with engine.cycle("test"):
        done = threading.Event()
        threading.Thread(
            target=lambda: (engine.close_position_by_id(pos["id"]), done.set()), daemon=True
        ).start()
        assert not done.wait(0.4), "잠금 중에도 수동 청산이 진행됐습니다"
    assert done.wait(20), "잠금 해제 후 수동 청산이 완료되지 않았습니다"


# ----------------------------------------------------------------------
# Reconciliation
# ----------------------------------------------------------------------
def test_reconcile_is_a_noop_when_consistent(store: UpbitStore, client: FakeUpbitClient) -> None:
    engine = build(store, client)
    engine.run_selection(dry_run=False)
    n = len(store.list_open_positions("paper"))

    result = engine.reconcile()
    assert result["checked"] == n
    assert result["released"] == [] and result["adjusted"] == []
    assert len(store.list_open_positions("paper")) == n


def test_reconcile_releases_a_phantom_position(store: UpbitStore, client: FakeUpbitClient) -> None:
    """Crash between order and DB write, or a manual sell in the Upbit app."""
    engine = build(store, client)
    engine.run_selection(dry_run=False)
    pos = store.list_open_positions("paper")[0]

    # Coins vanish from the exchange behind the engine's back.
    state = engine.broker._state()
    state["coins"].pop(pos["symbol"], None)
    engine.broker._save(state)

    result = engine.reconcile()
    assert [r["market"] for r in result["released"]] == [pos["market"]]
    remaining = {p["market"] for p in store.list_open_positions("paper")}
    assert pos["market"] not in remaining

    closed = [c for c in store.list_closed_positions(mode="paper") if c["market"] == pos["market"]]
    assert closed and closed[0]["exit_reason"] == "balance_missing"
    # Released, not sold — no phantom ask trade may be recorded.
    assert not [t for t in store.list_trades(mode="paper", side="ask") if t["market"] == pos["market"]]


def test_reconcile_shrinks_a_partially_sold_position(
    store: UpbitStore, client: FakeUpbitClient
) -> None:
    engine = build(store, client)
    engine.run_selection(dry_run=False)
    pos = store.list_open_positions("paper")[0]

    state = engine.broker._state()
    state["coins"][pos["symbol"]]["volume"] = float(pos["volume"]) * 0.4
    engine.broker._save(state)

    result = engine.reconcile()
    assert [a["market"] for a in result["adjusted"]] == [pos["market"]]
    updated = next(p for p in store.list_open_positions("paper") if p["id"] == pos["id"])
    assert updated["volume"] == pytest.approx(float(pos["volume"]) * 0.4)


def test_reconcile_never_grows_a_position(store: UpbitStore, client: FakeUpbitClient) -> None:
    """Extra balance is the user's, not the engine's — exposure must not appear."""
    engine = build(store, client)
    engine.run_selection(dry_run=False)
    pos = store.list_open_positions("paper")[0]

    state = engine.broker._state()
    state["coins"][pos["symbol"]]["volume"] = float(pos["volume"]) * 5
    engine.broker._save(state)

    engine.reconcile()
    updated = next(p for p in store.list_open_positions("paper") if p["id"] == pos["id"])
    assert updated["volume"] == pytest.approx(float(pos["volume"]))


def test_reconcile_respects_long_term_protection(
    store: UpbitStore, client: FakeUpbitClient
) -> None:
    engine = build(store, client)
    engine.run_selection(dry_run=False)
    pos = store.list_open_positions("paper")[0]

    add_holding(pos["symbol"], 0, "뒤늦게 장기보유로 전환", store=store)
    result = engine.reconcile()

    assert [r["market"] for r in result["released"]] == [pos["market"]]
    closed = [c for c in store.list_closed_positions(mode="paper") if c["market"] == pos["market"]]
    assert closed[0]["exit_reason"] == "long_term_protected"
    # The coins themselves stay put.
    assert engine.broker.coin_balance(pos["symbol"]) > 0


# ----------------------------------------------------------------------
# Liveness / health
# ----------------------------------------------------------------------
def test_health_is_ok_when_schedule_is_disabled(store: UpbitStore) -> None:
    store.set_override("config", {"schedule": {"enabled": False}})
    health = UpbitScheduler(store).health()
    assert health["healthy"] is True
    assert health["problems"] == []


def test_health_flags_a_dead_scheduler(store: UpbitStore) -> None:
    sched = UpbitScheduler(store)          # enabled by default, never started
    health = sched.health()
    assert health["healthy"] is False
    assert any("실행 중이 아닙니다" in p for p in health["problems"])


def test_health_flags_a_stale_monitor(store: UpbitStore) -> None:
    sched = UpbitScheduler(store)
    sched._scheduler = type("S", (), {"running": True})()
    sched._started_at = datetime.now(KST) - timedelta(hours=2)

    interval = 5
    state = sched.cycle_state("monitor")
    state.last_success_at = datetime.now(KST) - timedelta(
        minutes=interval * STALE_INTERVAL_MULTIPLIER + 5
    )
    health = sched.health()
    assert health["healthy"] is False
    assert any("모니터링" in p for p in health["problems"])

    state.last_success_at = datetime.now(KST)
    assert sched.health()["healthy"] is True


def test_health_tolerates_a_fresh_boot(store: UpbitStore) -> None:
    """A container that just started has no successful cycle yet — not a fault."""
    sched = UpbitScheduler(store)
    sched._scheduler = type("S", (), {"running": True})()
    sched._started_at = datetime.now(KST)
    assert sched.health()["healthy"] is True


def test_health_flags_repeated_job_failures(store: UpbitStore) -> None:
    sched = UpbitScheduler(store)
    sched._scheduler = type("S", (), {"running": True})()
    sched._started_at = datetime.now(KST)
    sched.cycle_state("selection").consecutive_failures = 3
    health = sched.health()
    assert health["healthy"] is False
    assert any("selection" in p for p in health["problems"])


def test_cycle_state_tracks_success_and_failure(store: UpbitStore) -> None:
    sched = UpbitScheduler(store)

    sched._guarded("probe", lambda: {"ok": True})
    state = sched.cycle_state("probe")
    assert state.runs == 1 and state.consecutive_failures == 0 and state.last_success_at

    def boom():
        raise RuntimeError("업비트 응답 없음")

    sched._guarded("probe", boom)
    sched._guarded("probe", boom)
    assert sched.cycle_state("probe").consecutive_failures == 2
    assert "업비트 응답 없음" in sched.cycle_state("probe").last_error

    sched._guarded("probe", lambda: {"ok": True})
    assert sched.cycle_state("probe").consecutive_failures == 0


def test_engine_reported_error_counts_as_a_failure(store: UpbitStore) -> None:
    """run_selection swallows its own exception and returns {'error': ...}."""
    sched = UpbitScheduler(store)
    sched._guarded("selection", lambda: {"run_id": 1, "error": "boom"})
    assert sched.cycle_state("selection").consecutive_failures == 1


# ----------------------------------------------------------------------
# Maintenance
# ----------------------------------------------------------------------
def _candidate(market: str) -> Candidate:
    return Candidate(
        market=market, symbol=market.split("-")[-1], price=1000.0,
        score=ScoreBreakdown(total=70.0, metrics={"rsi_14": 55.0}),
    )


def test_prune_drops_aged_rows_but_keeps_trades(store: UpbitStore) -> None:
    old = (utc_now() - timedelta(days=400)).isoformat(timespec="seconds")
    run_id = store.start_run("selection", "paper")
    store.save_analyses(run_id, old, [_candidate("KRW-XRP")])
    store.save_analyses(run_id, utc_now().isoformat(timespec="seconds"), [_candidate("KRW-SOL")])
    store.record_trade(mode="paper", market="KRW-XRP", symbol="XRP", side="bid",
                       ord_type="market", state="done", ts=old)
    with store.connect() as c:
        c.execute("INSERT INTO event_log (ts, level, category, message) VALUES (?,?,?,?)",
                  (old, "info", "test", "오래된 로그"))

    removed = store.prune(analyses_days=180, events_days=90, snapshots_days=730)
    assert removed["analyses"] == 1
    assert removed["event_log"] >= 1
    assert len(store.list_analyses(run_id=run_id)) == 1
    assert len(store.list_trades(mode="paper")) == 1, "거래 기록은 절대 지워지면 안 됩니다"


def test_prune_keeps_recent_operational_runs(store: UpbitStore) -> None:
    """Today's monitor cycles must survive — they are what you read after a fault."""
    recent = store.start_run("monitor", "paper")
    store.finish_run(recent, status="ok", note="오늘 모니터링")

    old_id = store.start_run("monitor", "paper")
    aged = (utc_now() - timedelta(days=400)).isoformat(timespec="seconds")
    with store.connect() as c:
        c.execute("UPDATE runs SET started_at=? WHERE id=?", (aged, old_id))

    removed = store.prune(analyses_days=180, events_days=90, snapshots_days=730)
    assert removed["runs"] == 1
    surviving = {r["id"] for r in store.list_runs(limit=50)}
    assert recent in surviving and old_id not in surviving


def test_prune_with_zero_retention_keeps_everything(store: UpbitStore) -> None:
    run_id = store.start_run("selection", "paper")
    store.save_analyses(run_id, (utc_now() - timedelta(days=999)).isoformat(), [_candidate("KRW-XRP")])
    removed = store.prune(analyses_days=0, events_days=0, snapshots_days=0)
    assert removed.get("analyses", 0) == 0
    assert removed.get("runs", 0) == 0
    assert len(store.list_analyses(run_id=run_id)) == 1


def test_backup_is_restorable_and_rotates(store: UpbitStore, tmp_path) -> None:
    store.log_event("info", "test", "백업 대상 데이터")
    backup_dir = tmp_path / "backups"

    paths = []
    for _ in range(3):
        paths.append(store.backup(backup_dir, keep=2))
        time.sleep(1.05)          # filenames are second-resolution

    assert len(list(backup_dir.glob("upbit-*.sqlite"))) == 2, "오래된 백업이 정리되지 않았습니다"
    restored = UpbitStore(paths[-1])
    assert any(e["message"] == "백업 대상 데이터" for e in restored.list_events())


def test_maintenance_job_runs_end_to_end(store: UpbitStore, tmp_path, monkeypatch) -> None:
    from upbit import scheduler as sched_mod

    monkeypatch.setattr(sched_mod, "default_db_path", lambda: tmp_path / "upbit.sqlite")
    store.log_event("info", "test", "유지보수 대상")

    result = UpbitScheduler(store)._maintenance()
    assert result["backup"] and (tmp_path / "backups").exists()
    assert "removed" in result and result["db_bytes_after"] > 0
    assert any(e["category"] == "maintenance" for e in store.list_events())


# ----------------------------------------------------------------------
# Watchdog — self-healing when the scheduler thread dies
# ----------------------------------------------------------------------
def test_watchdog_revives_a_dead_scheduler(store: UpbitStore, monkeypatch) -> None:
    """APScheduler survives job exceptions, but a hard executor failure would
    leave HTTP serving while nothing trades. Silence is the worst outcome."""
    from upbit import scheduler as sched_mod

    monkeypatch.setattr(sched_mod, "WATCHDOG_INTERVAL_SEC", 0.2)
    store.set_override("config", {"schedule": {"enabled": True, "monitor_interval_min": 60}})

    sched = UpbitScheduler(store)
    sched._engine = _StubEngine()   # keep the scheduler tests off the network

    try:
        sched.start()
        assert sched.status()["running"] is True

        # Simulate the scheduler dying underneath us — no config change, so this
        # is a fault rather than a deliberate stop.
        sched._scheduler.shutdown(wait=False)
        assert sched._scheduler.running is False
        assert sched.health()["healthy"] is False

        deadline = time.time() + 15
        while time.time() < deadline and not sched.status()["running"]:
            time.sleep(0.2)

        assert sched.status()["running"] is True, "워치독이 스케줄러를 되살리지 못했습니다"
        assert sched._watchdog_revivals >= 1
        assert any("재기동" in e["message"] for e in store.list_events())
    finally:
        sched.stop(wait=False)


def test_watchdog_leaves_a_deliberately_disabled_schedule_alone(
    store: UpbitStore, monkeypatch
) -> None:
    """Turning the schedule off in the dashboard must stay off."""
    from upbit import scheduler as sched_mod

    monkeypatch.setattr(sched_mod, "WATCHDOG_INTERVAL_SEC", 0.2)
    sched = UpbitScheduler(store)
    sched._engine = _StubEngine()
    store.set_override("config", {"schedule": {"enabled": True}})
    sched.start()
    try:
        store.set_override("config", {"schedule": {"enabled": False}})
        sched._scheduler.shutdown(wait=False)
        time.sleep(1.0)
        assert sched.status()["running"] is False
        assert sched._watchdog_revivals == 0
    finally:
        sched.stop(wait=False)


class _StubEngine:
    """Stands in for TradingEngine so scheduler tests never touch the network."""

    def reconcile(self):
        return {"checked": 0, "released": [], "adjusted": []}

    def run_selection(self, **kw):
        return {"scanned": 0}

    def monitor_positions(self, **kw):
        return {"checked": 0, "closed": []}

    def snapshot_equity(self):
        return {"total_krw": 0}
