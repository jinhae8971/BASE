"""Run artifact -- one structured JSON dump per DailyWorkflow execution.

Purpose:
  - complete audit trail for regulatory/tax review,
  - deterministic replay of any historical day (Phase 6 learning loop
    reads these to rebuild agent contexts for post-mortem),
  - human debugging when a run misbehaves,
  - input to future Optuna sweeps and backtest regressions.

Storage: `data/runs/<YYYY-MM-DD>/<run_id>.json`. Gitignored.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.agents.base import AgentResult

RUNS_ROOT = Path("data/runs")


@dataclass
class RunArtifact:
    run_id: str
    started_at: datetime
    ended_at: datetime
    halted: bool
    halt_reason: str | None
    universe: list[str]
    snapshot_errors: dict[str, str]
    elo_weights: dict[str, float]
    agent_results: list[dict[str, Any]]
    allocation: dict[str, Any]
    proposed_orders: list[dict[str, Any]]
    approved_orders: list[dict[str, Any]]
    fills: list[dict[str, Any]]
    equity_usd: float
    macro_regime: str
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "duration_ms": int((self.ended_at - self.started_at).total_seconds() * 1000),
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "universe": self.universe,
            "snapshot_errors": self.snapshot_errors,
            "elo_weights": self.elo_weights,
            "agent_results": self.agent_results,
            "allocation": self.allocation,
            "proposed_orders": self.proposed_orders,
            "approved_orders": self.approved_orders,
            "fills": self.fills,
            "equity_usd": self.equity_usd,
            "macro_regime": self.macro_regime,
            "errors": self.errors,
        }


def serialize_agent_result(result: AgentResult) -> dict[str, Any]:
    return {
        "agent": result.agent,
        "model": result.model,
        "payload": result.payload,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cached_tokens": result.cached_tokens,
        "cost_usd": result.cost_usd,
        "latency_ms": result.latency_ms,
    }


def write(artifact: RunArtifact, root: Path | None = None) -> Path:
    base = (root or RUNS_ROOT) / artifact.started_at.strftime("%Y-%m-%d")
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{artifact.run_id}.json"
    path.write_text(
        json.dumps(artifact.to_dict(), default=str, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
