"""Phase 3 orchestrator integration: graceful degradation, lessons RAG,
artifact writing, scheduler loop."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.agents.base import AgentContext
from src.agents.research import ResearchAgent
from src.data.binance_md import Candle
from src.data.coingecko import MarketRow
from src.data.fred import MacroSnapshot
from src.data.snapshot import MarketSnapshot
from src.llm import MockCall, MockLLMClient
from src.memory.vector_store import InMemoryLessonStore, Lesson
from src.orchestrator.daily_workflow import DailyWorkflow
from src.orchestrator.run_artifact import RUNS_ROOT
from src.orchestrator.scheduler import DailyScheduler


def _snapshot() -> MarketSnapshot:
    now = datetime.now(UTC)
    snap = MarketSnapshot(as_of=now)
    snap.universe = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    snap.candles = {
        sym: [Candle(now, 100, 101, 99, 100.5, 1000, 100_000) for _ in range(30)]
        for sym in snap.universe
    }
    snap.markets = [
        MarketRow("bitcoin", "BTC", "Bitcoin", 1e12, 1.05e12, 5e10, 1.0, 3.0),
    ]
    snap.macro = MacroSnapshot(values={"VIX": 14.0}, as_of={})
    return snap


def _build_full_mock() -> MockLLMClient:
    mock = MockLLMClient()
    mock.register("emit_research", lambda c: {
        "coins": {s: {"sentiment": 0.2, "narrative_strength": 0.4, "risk_flags": []}
                  for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]},
        "top_narratives": [],
    })
    mock.register("emit_macro", lambda c: {
        "regime": "neutral", "btc_bias": 0.0,
        "leverage_cap": 0.8, "cash_floor_pct": 15.0, "notes": "",
    })
    mock.register("emit_sector", lambda c: {
        "sector_scores": {k: 0.0 for k in ["L1", "L2", "DeFi", "AI", "RWA", "Gaming", "Meme"]},
        "hot_sectors": [], "rotation_signal": "none",
    })
    mock.register("emit_value", lambda c: {
        "coins": {s: {"fair_value_ratio": 1.0, "conviction": 0.3, "horizon_days": 90}
                  for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]},
    })
    mock.register("emit_quant", lambda c: {
        "coins": {s: {"signal": 0.3, "vol_target": 0.02, "stop_pct": 8.0}
                  for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]},
        "vol_regime": "normal",
    })
    mock.register("emit_executor", lambda c: {
        "target_weights": {"BTCUSDT": 40.0, "ETHUSDT": 25.0, "SOLUSDT": 10.0},
        "cash_pct": 25.0,
        "orders": [{"symbol": "BTCUSDT", "side": "BUY", "qty_usd": 200.0,
                    "type": "MARKET", "reason": "core"}],
        "rationale": "test",
    })
    return mock


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


async def test_one_failing_agent_falls_back_to_stub_and_run_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.orchestrator.run_artifact.RUNS_ROOT", tmp_path / "runs")

    mock = _build_full_mock()
    # Make the Research tool raise.
    mock.register("emit_research", lambda c: (_ for _ in ()).throw(RuntimeError("boom")))

    async def fake_snap(**kw):  # noqa: ANN003
        return _snapshot()

    wf = DailyWorkflow(snapshot_fn=fake_snap, llm_client=mock)
    result = await wf.run(universe_size=3)

    assert result["halted"] is False
    # Error recorded but run continued -> orders came from the executor.
    assert any("research" in e for e in result["errors"])
    assert len(result["orders"]) == 1
    assert result["orders"][0]["symbol"] == "BTCUSDT"


async def test_all_agents_failing_still_produces_safe_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.orchestrator.run_artifact.RUNS_ROOT", tmp_path / "runs")

    mock = MockLLMClient()
    for name in ("emit_research", "emit_macro", "emit_sector", "emit_value",
                 "emit_quant", "emit_executor"):
        mock.register(name, lambda c, _n=name: (_ for _ in ()).throw(RuntimeError(_n)))

    async def fake_snap(**kw):  # noqa: ANN003
        return _snapshot()

    wf = DailyWorkflow(snapshot_fn=fake_snap, llm_client=mock)
    result = await wf.run(universe_size=3)
    # With every agent falling back to stub, the executor stub emits no orders
    # and the run completes cleanly with errors recorded.
    assert result["orders"] == []
    assert len(result["errors"]) == 6  # all 6 agents recorded


# ---------------------------------------------------------------------------
# Lessons RAG injection
# ---------------------------------------------------------------------------


async def test_recent_lessons_reach_agent_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.orchestrator.run_artifact.RUNS_ROOT", tmp_path / "runs")
    store = InMemoryLessonStore()
    await store.add(Lesson(text="In risk-on markets, avoid small-caps after +50% weekly moves.",
                           tags=["risk-on", "meme"]))
    await store.add(Lesson(text="Don't fight macro regime shifts via single-coin conviction.",
                           tags=["macro"]))

    seen_lessons: list[list[str]] = []

    mock = _build_full_mock()

    def research_capturing(call: MockCall) -> dict:
        # The user message is JSON; parse it to assert lessons came through.
        import json
        user = json.loads(call.user)
        seen_lessons.append(user.get("lessons", []))
        return {
            "coins": {s: {"sentiment": 0.1, "narrative_strength": 0.3, "risk_flags": []}
                      for s in user.get("universe", [])},
            "top_narratives": [],
        }

    mock.register("emit_research", research_capturing)

    async def fake_snap(**kw):  # noqa: ANN003
        return _snapshot()

    wf = DailyWorkflow(snapshot_fn=fake_snap, llm_client=mock, lesson_store=store)
    await wf.run(universe_size=3)

    assert seen_lessons, "research agent should have been called"
    lessons = seen_lessons[0]
    assert len(lessons) == 2
    assert "risk-on" in lessons[0]


# ---------------------------------------------------------------------------
# Run artifact persistence
# ---------------------------------------------------------------------------


async def test_run_writes_artifact_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr("src.orchestrator.run_artifact.RUNS_ROOT", runs_dir)

    async def fake_snap(**kw):  # noqa: ANN003
        return _snapshot()

    wf = DailyWorkflow(snapshot_fn=fake_snap, llm_client=_build_full_mock())
    result = await wf.run(universe_size=3)

    assert result["artifact_path"] is not None
    artifact_path = Path(result["artifact_path"])
    assert artifact_path.exists()
    import json
    doc = json.loads(artifact_path.read_text())
    assert doc["run_id"] == result["run_id"]
    assert doc["universe"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert len(doc["agent_results"]) == 6  # 5 signal + executor
    assert doc["approved_orders"][0]["symbol"] == "BTCUSDT"


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


async def test_scheduler_runs_fixed_number_of_ticks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.orchestrator.run_artifact.RUNS_ROOT", tmp_path / "runs")

    async def fake_snap(**kw):  # noqa: ANN003
        return _snapshot()

    wf = DailyWorkflow(snapshot_fn=fake_snap, llm_client=_build_full_mock())

    sleep_calls: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)

    sched = DailyScheduler(
        workflow=wf,
        interval_seconds=3600,
        sleep_fn=fake_sleep,
        halt_check=lambda: False,
        max_ticks=3,
    )
    await sched.run()
    assert sched.runs_completed == 3
    assert sched.runs_failed == 0
    # Scheduler exits immediately after the final tick without sleeping, so
    # 3 ticks produce 2 sleep calls, not 3.
    assert sleep_calls == [3600.0, 3600.0]


async def test_scheduler_skips_run_when_halt_file_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.orchestrator.run_artifact.RUNS_ROOT", tmp_path / "runs")

    async def fake_snap(**kw):  # noqa: ANN003
        return _snapshot()

    wf = DailyWorkflow(snapshot_fn=fake_snap, llm_client=_build_full_mock())

    async def fake_sleep(secs: float) -> None:
        pass

    sched = DailyScheduler(
        workflow=wf,
        interval_seconds=1,
        sleep_fn=fake_sleep,
        halt_check=lambda: True,  # always halted
        max_ticks=2,
    )
    await sched.run()
    assert sched.runs_halted == 2
    assert sched.runs_completed == 0
