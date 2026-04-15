"""Run artifact round-trip."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.orchestrator.run_artifact import RunArtifact, load, write


def test_write_then_load_round_trip(tmp_path: Path) -> None:
    art = RunArtifact(
        run_id="abc123",
        started_at=datetime(2025, 4, 15, 12, 0, tzinfo=UTC),
        ended_at=datetime(2025, 4, 15, 12, 0, 5, tzinfo=UTC),
        halted=False,
        halt_reason=None,
        universe=["BTCUSDT", "ETHUSDT"],
        snapshot_errors={},
        elo_weights={"research": 0.2, "quant": 0.2, "macro": 0.2, "sector": 0.2, "value": 0.2},
        agent_results=[{"agent": "research", "model": "claude-sonnet-4-6", "payload": {}}],
        allocation={"weights": {"BTCUSDT": 40.0}, "cash_pct": 60.0},
        proposed_orders=[],
        approved_orders=[],
        fills=[],
        equity_usd=1000.0,
        macro_regime="neutral",
        errors=[],
    )

    path = write(art, root=tmp_path)
    assert path.exists()
    assert path.parent.name == "2025-04-15"
    assert path.name == "abc123.json"

    data = load(path)
    assert data["run_id"] == "abc123"
    assert data["universe"] == ["BTCUSDT", "ETHUSDT"]
    assert data["duration_ms"] == 5000
    assert data["halted"] is False
