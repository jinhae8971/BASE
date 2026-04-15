"""Multi-day backtest verifying the self-learning loop actually evolves.

Drives BacktestEngine with a custom MockLLMClient that deliberately churns
BTC (buy some days, sell others) so positions actually open and close over
the backtest window. Asserts that:
  - the engine processes at least one closed position,
  - at least one lesson is persisted into the shared lesson store,
  - the ELO weights at the end of the run differ from the default uniform
    weights (the learning loop shifted them),
  - the BacktestResult exposes the learning metrics for Phase 6+ dashboards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.portfolio_sim import SimConfig
from src.backtest.provider import HistoricalSnapshotProvider
from src.data.binance_md import Candle
from src.learning.elo import DEFAULT_RATING
from src.llm import MockCall, MockLLMClient


def _gen(n: int, start: float = 100.0, drift: float = 0.005) -> list[Candle]:
    t = datetime(2024, 1, 1, tzinfo=UTC)
    out: list[Candle] = []
    p = start
    for i in range(n):
        p *= 1 + drift
        out.append(
            Candle(
                open_time=t + timedelta(days=i),
                open=p * 0.999, high=p * 1.004, low=p * 0.996, close=p,
                volume=1_000, quote_volume=p * 1_000,
            )
        )
    return out


def _provider(n: int = 50) -> HistoricalSnapshotProvider:
    return HistoricalSnapshotProvider(
        universe=["BTCUSDT", "ETHUSDT"],
        candles_by_symbol={
            "BTCUSDT": _gen(n, 30_000.0, 0.005),
            "ETHUSDT": _gen(n, 2_000.0, 0.003),
        },
        warmup_candles=10,
    )


def _build_churning_mock() -> MockLLMClient:
    """Mock that alternates buying and selling BTC so positions close often.

    The executor tracks its own day counter via a mutable closure -- odd
    calls emit BUYs, even calls emit SELLs. That guarantees we observe
    closed positions and the learning loop runs.
    """
    mock = MockLLMClient()

    mock.register("emit_research", lambda c: {
        "coins": {s: {"sentiment": 0.1, "narrative_strength": 0.3, "risk_flags": []}
                  for s in ["BTCUSDT", "ETHUSDT"]},
        "top_narratives": [],
    })
    mock.register("emit_macro", lambda c: {
        "regime": "neutral", "btc_bias": 0.0,
        "leverage_cap": 0.8, "cash_floor_pct": 20.0, "notes": "",
    })
    mock.register("emit_sector", lambda c: {
        "sector_scores": {k: 0.0 for k in ["L1", "L2", "DeFi", "AI", "RWA", "Gaming", "Meme"]},
        "hot_sectors": [], "rotation_signal": "none",
    })
    mock.register("emit_value", lambda c: {
        "coins": {s: {"fair_value_ratio": 1.0, "conviction": 0.2, "horizon_days": 90}
                  for s in ["BTCUSDT", "ETHUSDT"]},
    })
    mock.register("emit_quant", lambda c: {
        "coins": {s: {"signal": 0.3, "vol_target": 0.02, "stop_pct": 8.0}
                  for s in ["BTCUSDT", "ETHUSDT"]},
        "vol_regime": "normal",
    })

    exec_call = {"n": 0}

    def executor(call: MockCall) -> dict:
        exec_call["n"] += 1
        if exec_call["n"] % 2 == 1:
            # BUY day: open/add to a BTC position.
            return {
                "target_weights": {"BTCUSDT": 30.0, "ETHUSDT": 0.0},
                "cash_pct": 70.0,
                "orders": [{"symbol": "BTCUSDT", "side": "BUY", "qty_usd": 150.0,
                            "type": "MARKET", "reason": "enter"}],
                "rationale": "open BTC",
            }
        # SELL day: fully close BTC.
        return {
            "target_weights": {"BTCUSDT": 0.0, "ETHUSDT": 0.0},
            "cash_pct": 100.0,
            "orders": [{"symbol": "BTCUSDT", "side": "SELL", "qty_usd": 10_000.0,
                        "type": "MARKET", "reason": "exit"}],
            "rationale": "close BTC",
        }

    mock.register("emit_executor", executor)

    # Reflection gives quant a positive delta on every close -- ELO should
    # drift toward quant as the backtest progresses.
    mock.register("emit_reflection", lambda c: {
        "lesson": "Quant trend-follow call in neutral regime landed a small win on BTC.",
        "agent_scores": {
            "research": 0.0, "macro": 0.0, "sector": 0.0,
            "value": 0.1, "quant": 0.9, "executor": 0.1,
        },
        "tags": ["neutral", "trend"],
    })

    return mock


async def test_backtest_processes_closed_positions_and_shifts_elo() -> None:
    engine = BacktestEngine(
        provider=_provider(n=40),
        llm_client=_build_churning_mock(),
        sim_config=SimConfig(initial_cash=1000.0, fee_bps=0.0, slippage_bps=0.0),
    )
    result = await engine.run()

    # Learning loop actually ran.
    assert result.num_closed_positions >= 2
    assert result.num_lessons >= 2

    # ELO drifted: quant's rating moved above the starting default rating.
    assert engine.elo.ratings["quant"] > DEFAULT_RATING
    assert result.final_elo_weights["quant"] > 1.0 / 5  # > uniform share

    # Lesson store accumulated entries.
    recent = await engine.lesson_store.recent(20)
    assert len(recent) == result.num_lessons
    assert any("trend" in le.tags for le in recent)
