"""Booster: extract training data, train model, propose nudges."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta


def _patch(monkeypatch, tmp_path) -> sqlite3.Connection:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    from common import config as c

    c.get_env.cache_clear()
    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {
            "memory": {"journal_db": str(tmp_path / "j.sqlite")},
            "consensus": {
                "weights": {
                    "macro": 0.20,
                    "sector": 0.15,
                    "value": 0.20,
                    "quant": 0.45,
                }
            },
        },
    )
    # Make sure the journal table exists
    from memory.journal import DecisionJournal

    DecisionJournal()
    return sqlite3.connect(tmp_path / "j.sqlite")


def _seed_picks(
    conn: sqlite3.Connection, agent: str, n: int, mean_outcome: float
) -> None:
    """Insert ``n`` PICK rows whose outcome_1m centers on ``mean_outcome``."""
    base = datetime.utcnow() - timedelta(days=60)
    for i in range(n):
        rid = f"{agent}-{i}"
        ctx = json.dumps({"score": float(1.0 + (i % 3))}, ensure_ascii=False)
        outcome = mean_outcome + (i % 5) * 0.001  # tiny variance
        conn.execute(
            "INSERT INTO decisions (id, ts, agent, action, ticker, conviction, "
            "rationale, context_json, outcome_1m) VALUES (?, ?, ?, 'PICK', ?, ?, '', ?, ?)",
            (rid, (base + timedelta(days=i)).isoformat(), agent, "0059300", 7, ctx, outcome),
        )
    conn.commit()


def test_extract_training_data_returns_per_agent(monkeypatch, tmp_path) -> None:
    conn = _patch(monkeypatch, tmp_path)
    _seed_picks(conn, "quant", 25, 0.02)
    _seed_picks(conn, "value", 25, -0.01)

    from learning.booster import extract_training_data

    data = extract_training_data(180)
    assert "quant" in data and "value" in data
    assert len(data["quant"]) == 25
    assert all("outcome_1m" in r for r in data["quant"])


def test_score_agents_ranks_by_outcome(monkeypatch, tmp_path) -> None:
    conn = _patch(monkeypatch, tmp_path)
    _seed_picks(conn, "quant", 30, 0.025)   # the winner
    _seed_picks(conn, "value", 30, 0.005)
    _seed_picks(conn, "macro", 30, -0.005)

    from learning.booster import score_agents

    out = score_agents(180)
    agents = [s.agent for s in out]
    assert agents.index("quant") < agents.index("value")
    assert agents.index("value") < agents.index("macro")


def test_propose_consensus_nudges_bounded(monkeypatch, tmp_path) -> None:
    conn = _patch(monkeypatch, tmp_path)
    _seed_picks(conn, "quant", 40, 0.05)    # huge alpha
    _seed_picks(conn, "macro", 40, -0.05)   # huge negative

    from learning.booster import propose_consensus_nudges

    nudges = propose_consensus_nudges(max_delta=0.05, min_samples=20)
    by_agent = {n.agent: n for n in nudges}
    # Quant should be boosted, macro cut — within bounds
    assert by_agent["quant"].delta > 0
    assert by_agent["macro"].delta < 0
    # Pre-normalisation clamp is 0.05; post-normalise can drift by a small
    # amount so accept a 30% buffer.
    assert all(abs(n.delta) <= 0.05 * 1.3 for n in nudges)
    # Total weight after should still sum to ~1.0 (rounded to 4dp by booster)
    assert abs(sum(n.new_weight for n in nudges) - 1.0) < 1e-3


def test_no_nudges_when_too_few_samples(monkeypatch, tmp_path) -> None:
    conn = _patch(monkeypatch, tmp_path)
    _seed_picks(conn, "quant", 5, 0.05)  # below default min_samples=20

    from learning.booster import propose_consensus_nudges

    nudges = propose_consensus_nudges(min_samples=20)
    assert nudges == []


def test_apply_appends_block_to_reflection(monkeypatch, tmp_path) -> None:
    conn = _patch(monkeypatch, tmp_path)
    _seed_picks(conn, "quant", 40, 0.04)

    refl_dir = tmp_path / "reflections"
    refl_dir.mkdir(parents=True, exist_ok=True)
    report = refl_dir / "2025-05-09.md"
    report.write_text("# Reflection\n\nNarrative.\n", encoding="utf-8")

    from learning.apply import append_to_latest_reflection

    out = append_to_latest_reflection()
    assert "patches" in out
    body = report.read_text(encoding="utf-8")
    assert "## Auto-apply patches" in body
    assert '"path": "consensus.weights.quant"' in body
