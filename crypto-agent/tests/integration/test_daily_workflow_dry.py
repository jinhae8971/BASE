"""End-to-end dry run. Asserts the full DAG completes with no real I/O."""

from __future__ import annotations

from src.orchestrator.daily_workflow import DailyWorkflow


async def test_dry_run_completes_without_orders() -> None:
    wf = DailyWorkflow()
    result = await wf.run(universe_size=5)
    assert "run_id" in result
    assert "allocation" in result
    assert "elo_weights" in result
    # Dry mode emits no orders because the executor stub returns [].
    assert result["orders"] == []
    # Aggregator weights from default ELO sum to ~1.
    assert abs(sum(result["elo_weights"].values()) - 1.0) < 1e-9
