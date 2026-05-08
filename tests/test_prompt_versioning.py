"""Verify each agent's prompt hash + journal column round-trips."""
from __future__ import annotations

import sqlite3

from agents import MacroAgent, QuantAgent, SectorAgent, ValueAgent
from memory.journal import DecisionJournal


def test_each_agent_has_distinct_prompt_version() -> None:
    versions = {
        a.name: a.prompt_version()
        for a in [MacroAgent(), SectorAgent(), ValueAgent(), QuantAgent()]
    }
    # All non-empty
    assert all(v and v != "no-prompt" for v in versions.values())
    # 4 different prompts → 4 different hashes
    assert len(set(versions.values())) == 4


def test_prompt_version_is_deterministic() -> None:
    a = MacroAgent.prompt_version()
    b = MacroAgent.prompt_version()
    assert a == b
    assert len(a) == 12


def test_journal_persists_prompt_version(tmp_path) -> None:
    db = tmp_path / "j.sqlite"
    j = DecisionJournal(db_path=db)
    j.record(
        agent="quant",
        action="PROPOSE",
        ticker=None,
        conviction=8,
        rationale="x",
        context={},
        prompt_version="abc123def456",
    )
    with sqlite3.connect(db) as c:
        cur = c.execute("SELECT prompt_version FROM decisions")
        row = cur.fetchone()
    assert row[0] == "abc123def456"


def test_journal_omitted_prompt_version_is_null(tmp_path) -> None:
    """Backward compat — old call sites without the kw still work."""
    db = tmp_path / "j.sqlite"
    j = DecisionJournal(db_path=db)
    j.record(
        agent="quant",
        action="PROPOSE",
        ticker=None,
        conviction=5,
        rationale="x",
        context={},
    )
    with sqlite3.connect(db) as c:
        cur = c.execute("SELECT prompt_version FROM decisions")
        row = cur.fetchone()
    assert row[0] is None
