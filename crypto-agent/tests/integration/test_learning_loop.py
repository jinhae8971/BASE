"""Self-learning loop integration tests.

Verify that a closed position triggers the Reflection agent, persists a
lesson into the vector store, and updates the ELO table — all through a
MockLLMClient so the tests stay hermetic.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agents.base import AgentContext
from src.data.binance_md import Candle
from src.data.coingecko import MarketRow
from src.data.fred import MacroSnapshot
from src.data.snapshot import MarketSnapshot
from src.execution.binance_client import Fill, Order
from src.learning.elo import EloTable
from src.learning.learning_loop import LearningLoop
from src.learning.position_tracker import (
    ClosedPosition,
    EntryContext,
    PositionTracker,
)
from src.llm import MockCall, MockLLMClient
from src.memory.vector_store import InMemoryLessonStore
from src.orchestrator.daily_workflow import DailyWorkflow


def _closed() -> ClosedPosition:
    entry = EntryContext(
        run_id="r1",
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
        agent_payloads={"quant": {"signal": 0.6}, "macro": {"regime": "risk-on"}},
        macro_regime="risk-on",
        universe=["BTCUSDT"],
    )
    return ClosedPosition(
        symbol="BTCUSDT",
        avg_entry_price=100.0,
        exit_price=110.0,
        total_qty=1.0,
        opened_at=datetime(2025, 1, 1, tzinfo=UTC),
        closed_at=datetime(2025, 1, 10, tzinfo=UTC),
        realized_pnl_usd=10.0,
        entry=entry,
    )


async def test_learning_loop_stores_lesson_and_shifts_elo() -> None:
    lessons_store = InMemoryLessonStore()
    elo = EloTable()
    quant_before = elo.weights()["quant"]

    def reflection_handler(call: MockCall) -> dict:
        return {
            "lesson": "Risk-on + positive quant momentum worked for BTC; keep trusting quant in this regime.",
            "agent_scores": {
                "research": 0.1,
                "macro": 0.2,
                "sector": 0.0,
                "value": 0.0,
                "quant": 0.8,      # big win
                "executor": 0.0,
            },
            "tags": ["risk-on", "momentum", "BTC"],
        }

    mock = MockLLMClient(handlers={"emit_reflection": reflection_handler})
    loop = LearningLoop(elo=elo, lesson_store=lessons_store, llm_client=mock)

    payload = await loop.on_closed(_closed())

    assert payload is not None
    # Lesson persisted with trade_id linking back to the entry run + symbol.
    stored = await lessons_store.recent(10)
    assert len(stored) == 1
    assert "quant" in stored[0].text.lower()
    assert stored[0].trade_id == "r1:BTCUSDT"
    # ELO for quant increased because it got the highest delta.
    quant_after = elo.weights()["quant"]
    assert quant_after > quant_before
    assert loop.closures_processed == 1
    assert loop.lessons_written == 1


async def test_learning_loop_swallows_reflection_failure() -> None:
    def broken_handler(call: MockCall) -> dict:
        raise RuntimeError("reflection model timeout")

    mock = MockLLMClient(handlers={"emit_reflection": broken_handler})
    lessons_store = InMemoryLessonStore()
    elo = EloTable()

    loop = LearningLoop(elo=elo, lesson_store=lessons_store, llm_client=mock)
    result = await loop.on_closed(_closed())
    assert result is None
    assert loop.closures_processed == 0
    assert loop.lessons_written == 0


# ---------------------------------------------------------------------------
# End-to-end: DailyWorkflow run that contains a SELL which closes a pre-
# existing position should trigger the learning loop and shift ELO.
# ---------------------------------------------------------------------------


def _snapshot() -> MarketSnapshot:
    now = datetime.now(UTC)
    snap = MarketSnapshot(as_of=now)
    snap.universe = ["BTCUSDT", "ETHUSDT"]
    snap.candles = {
        sym: [Candle(now, 100, 101, 99, 100.5, 1000, 100_000) for _ in range(30)]
        for sym in snap.universe
    }
    snap.markets = [
        MarketRow("bitcoin", "BTC", "Bitcoin", 1e12, 1.05e12, 5e10, 1.0, 3.0),
    ]
    snap.macro = MacroSnapshot(values={"VIX": 14.0}, as_of={})
    return snap


def _build_selling_mock() -> MockLLMClient:
    mock = MockLLMClient()
    # Neutral signal agents.
    mock.register("emit_research", lambda c: {
        "coins": {s: {"sentiment": 0.1, "narrative_strength": 0.3, "risk_flags": []}
                  for s in ["BTCUSDT", "ETHUSDT"]},
        "top_narratives": [],
    })
    mock.register("emit_macro", lambda c: {
        "regime": "risk-off", "btc_bias": -0.2,
        "leverage_cap": 0.3, "cash_floor_pct": 50.0, "notes": "",
    })
    mock.register("emit_sector", lambda c: {
        "sector_scores": {k: 0.0 for k in ["L1", "L2", "DeFi", "AI", "RWA", "Gaming", "Meme"]},
        "hot_sectors": [], "rotation_signal": "none",
    })
    mock.register("emit_value", lambda c: {
        "coins": {s: {"fair_value_ratio": 1.0, "conviction": 0.0, "horizon_days": 90}
                  for s in ["BTCUSDT", "ETHUSDT"]},
    })
    mock.register("emit_quant", lambda c: {
        "coins": {s: {"signal": 0.0, "vol_target": 0.02, "stop_pct": 8.0}
                  for s in ["BTCUSDT", "ETHUSDT"]},
        "vol_regime": "normal",
    })
    # Executor emits a SELL of a pre-existing BTC position.
    mock.register("emit_executor", lambda c: {
        "target_weights": {"BTCUSDT": 0.0, "ETHUSDT": 0.0},
        "cash_pct": 100.0,
        "orders": [{"symbol": "BTCUSDT", "side": "SELL", "qty_usd": 200.0,
                    "type": "MARKET", "reason": "macro flipped risk-off"}],
        "rationale": "close BTC on risk-off flip",
    })
    mock.register("emit_reflection", lambda c: {
        "lesson": "Selling BTC on risk-off flip locked in a small win.",
        "agent_scores": {
            "research": 0.0, "macro": 0.5, "sector": 0.0,
            "value": 0.0, "quant": -0.2, "executor": 0.3,
        },
        "tags": ["risk-off", "BTC", "exit"],
    })
    return mock


class _SellFriendlyBinance:
    """Minimal binance stub that owns a BTC position at a known entry price."""

    def __init__(self) -> None:
        self.fills: list[Fill] = []
        self._held_qty = 2.0
        self._entry_price = 100.0
        self._current_price = 110.0

    async def submit(self, order: Order) -> Fill:
        fill = Fill(
            symbol=order.symbol,
            side=order.side,
            qty=self._held_qty if order.side == "SELL" else (order.qty_usd / self._current_price),
            price=self._current_price,
            fee_usd=order.qty_usd * 0.001,
            filled_at=datetime.now(UTC),
        )
        self.fills.append(fill)
        return fill

    async def account_equity_usd(self) -> float:
        return 1000.0


async def test_daily_workflow_triggers_learning_on_close() -> None:
    mock = _build_selling_mock()
    lesson_store = InMemoryLessonStore()
    elo = EloTable()
    tracker = PositionTracker()

    # Pre-seed: a BTC position was opened by an earlier run.
    tracker.on_buy(
        "BTCUSDT",
        qty=2.0,
        price=100.0,
        at=datetime(2025, 1, 1, tzinfo=UTC),
        entry=EntryContext(
            run_id="prev-run",
            as_of=datetime(2025, 1, 1, tzinfo=UTC),
            agent_payloads={"quant": {"signal": 0.8}},
            macro_regime="risk-on",
            universe=["BTCUSDT", "ETHUSDT"],
        ),
    )

    async def fake_snap(**kw):  # noqa: ANN003
        return _snapshot()

    wf = DailyWorkflow(
        elo=elo,
        binance=_SellFriendlyBinance(),  # type: ignore[arg-type]
        snapshot_fn=fake_snap,
        llm_client=mock,
        lesson_store=lesson_store,
        position_tracker=tracker,
    )
    macro_weight_before = elo.weights()["macro"]

    await wf.run(universe_size=2)

    # BTC closed -> reflection ran -> lesson stored -> ELO shifted.
    assert not tracker.is_open("BTCUSDT")
    lessons = await lesson_store.recent(10)
    assert len(lessons) == 1
    assert "risk-off" in lessons[0].tags
    # Macro scored highest in reflection, so its weight went up.
    assert elo.weights()["macro"] > macro_weight_before
