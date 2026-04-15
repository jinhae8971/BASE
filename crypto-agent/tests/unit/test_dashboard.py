"""Dashboard generator unit tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.dashboard.generator import (
    DashboardStats,
    aggregate,
    generate_dashboard,
    load_run_artifacts,
    render_html,
)


def _artifact(
    run_id: str,
    *,
    halted: bool = False,
    equity: float = 1000.0,
    errors: list[str] | None = None,
    started_at: str = "2025-01-01T12:00:00+00:00",
    orders: int = 0,
    agent_cost: float = 0.0,
    regime: str = "neutral",
    elo: dict[str, float] | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": started_at,
        "duration_ms": 1000,
        "halted": halted,
        "halt_reason": None,
        "universe": ["BTCUSDT", "ETHUSDT"],
        "snapshot_errors": {},
        "elo_weights": elo or {},
        "agent_results": [{"agent": "a", "cost_usd": agent_cost}],
        "allocation": {},
        "proposed_orders": [],
        "approved_orders": [{"symbol": "BTCUSDT"}] * orders,
        "fills": [],
        "equity_usd": equity,
        "macro_regime": regime,
        "errors": errors or [],
    }


# ---------------------------------------------------------------------------
# load_run_artifacts
# ---------------------------------------------------------------------------


def test_load_run_artifacts_walks_dated_subdirs(tmp_path: Path) -> None:
    (tmp_path / "2025-01-01").mkdir()
    (tmp_path / "2025-01-02").mkdir()
    (tmp_path / "2025-01-01" / "abc.json").write_text(
        json.dumps(_artifact("abc", started_at="2025-01-01T09:00:00+00:00"))
    )
    (tmp_path / "2025-01-02" / "def.json").write_text(
        json.dumps(_artifact("def", started_at="2025-01-02T09:00:00+00:00"))
    )

    arts = load_run_artifacts(tmp_path)
    assert [a["run_id"] for a in arts] == ["abc", "def"]


def test_load_run_artifacts_empty_root(tmp_path: Path) -> None:
    assert load_run_artifacts(tmp_path / "missing") == []


def test_load_run_artifacts_skips_malformed_files(tmp_path: Path) -> None:
    (tmp_path / "2025-01-01").mkdir()
    (tmp_path / "2025-01-01" / "good.json").write_text(json.dumps(_artifact("good")))
    (tmp_path / "2025-01-01" / "bad.json").write_text("not json at all")

    arts = load_run_artifacts(tmp_path)
    assert len(arts) == 1
    assert arts[0]["run_id"] == "good"


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


def test_aggregate_empty_returns_zeros() -> None:
    stats = aggregate([])
    assert stats.total_runs == 0
    assert stats.latest_equity == 0.0
    assert stats.equity_curve == []


def test_aggregate_counts_halted_and_errors_separately() -> None:
    arts = [
        _artifact("r1", equity=1000.0, started_at="2025-01-01T09:00:00+00:00"),
        _artifact("r2", halted=True, started_at="2025-01-02T09:00:00+00:00"),
        _artifact("r3", equity=1050.0, errors=["boom"],
                  started_at="2025-01-03T09:00:00+00:00"),
    ]
    stats = aggregate(arts)
    assert stats.total_runs == 3
    assert stats.halted_runs == 1
    assert stats.runs_with_errors == 1
    assert stats.completed_runs == 2
    # Equity curve skips halted.
    assert len(stats.equity_curve) == 2
    assert stats.latest_equity == 1050.0
    assert stats.latest_run_id == "r3"


def test_aggregate_sums_llm_cost_across_runs() -> None:
    arts = [
        _artifact("r1", agent_cost=0.001, started_at="2025-01-01T09:00:00+00:00"),
        _artifact("r2", agent_cost=0.002, started_at="2025-01-02T09:00:00+00:00"),
    ]
    stats = aggregate(arts)
    assert stats.cumulative_llm_cost_usd == pytest.approx(0.003)


def test_aggregate_caps_recent_errors_at_ten() -> None:
    arts = []
    for i in range(15):
        arts.append(
            _artifact(
                f"r{i}",
                errors=[f"err{i}"],
                started_at=f"2025-01-{i+1:02d}T09:00:00+00:00",
            )
        )
    stats = aggregate(arts)
    assert len(stats.recent_errors) == 10


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------


def test_render_html_contains_all_sections_for_non_empty_stats() -> None:
    arts = [
        _artifact(
            "abcdef12",
            equity=1234.56,
            started_at="2025-01-01T09:00:00+00:00",
            elo={"research": 0.25, "quant": 0.20},
            orders=2,
        )
    ]
    html_text = render_html(aggregate(arts), arts)
    assert "crypto-agent dashboard" in html_text
    assert "$1,234.56" in html_text  # formatted latest equity
    assert "abcdef12" in html_text[:2000] or "abcdef12"[:8] in html_text
    assert "Equity curve" in html_text
    assert "ELO agent weights" in html_text
    assert "Recent runs" in html_text
    # The SVG "need at least 2 runs" fallback path fires for a single run.
    assert "need at least 2 runs" in html_text


def test_render_html_escapes_user_text() -> None:
    arts = [
        _artifact(
            "r1",
            errors=["<script>alert(1)</script>"],
            started_at="2025-01-01T09:00:00+00:00",
        )
    ]
    html_text = render_html(aggregate(arts), arts)
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;" in html_text


def test_render_html_draws_curve_when_two_runs_present() -> None:
    arts = [
        _artifact("r1", equity=1000.0, started_at="2025-01-01T09:00:00+00:00"),
        _artifact("r2", equity=1100.0, started_at="2025-01-02T09:00:00+00:00"),
    ]
    html_text = render_html(aggregate(arts), arts)
    # SVG <path> element is drawn when there are >= 2 data points.
    assert "<path " in html_text
    assert "2025-01-01" in html_text
    assert "2025-01-02" in html_text


# ---------------------------------------------------------------------------
# generate_dashboard CLI glue
# ---------------------------------------------------------------------------


def test_generate_dashboard_writes_html_to_output_path(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    (runs_root / "2025-01-01").mkdir()
    (runs_root / "2025-01-01" / "x.json").write_text(
        json.dumps(_artifact("x", started_at="2025-01-01T09:00:00+00:00"))
    )
    out = tmp_path / "out" / "index.html"
    path = generate_dashboard(runs_root=runs_root, output=out)
    assert path == out
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "<!doctype html>" in content
    assert "crypto-agent" in content
