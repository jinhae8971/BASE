from __future__ import annotations

from memory.journal import DecisionJournal


def test_record_and_fetch(tmp_path) -> None:
    db = tmp_path / "j.sqlite"
    j = DecisionJournal(db_path=db)
    rec = j.record(
        agent="macro",
        action="PROPOSE",
        ticker=None,
        conviction=7,
        rationale="risk-on",
        context={"vix": 14},
    )
    assert rec.agent_name == "macro"
    rows = j.recent(lookback_days=1)
    assert len(rows) == 1
    assert rows[0]["agent"] == "macro"
