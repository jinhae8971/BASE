from __future__ import annotations

from datetime import datetime, timedelta

from memory.journal import DecisionJournal


def test_pending_outcomes_returns_old_ticker_decisions(tmp_path) -> None:
    db = tmp_path / "j.sqlite"
    j = DecisionJournal(db_path=db)
    rec = j.record(
        agent="quant",
        action="PICK",
        ticker="005930",
        conviction=8,
        rationale="x",
        context={},
    )
    # Backdate the row 10 days into the past so it's eligible for 1w outcome.
    import sqlite3

    old_ts = (datetime.utcnow() - timedelta(days=10)).isoformat()
    with sqlite3.connect(db) as c:
        c.execute("UPDATE decisions SET ts = ? WHERE id = ?", (old_ts, rec.id))
    pending = j.pending_outcomes("1w")
    assert any(p["id"] == rec.id for p in pending)


def test_update_outcome_persists(tmp_path) -> None:
    db = tmp_path / "j.sqlite"
    j = DecisionJournal(db_path=db)
    rec = j.record(
        agent="quant",
        action="PICK",
        ticker="005930",
        conviction=8,
        rationale="x",
        context={},
    )
    j.update_outcome(rec.id, outcome_1w=0.05, outcome_1m=0.10)
    rows = j.find_by_ticker("005930")
    assert rows[0]["outcome_1w"] == 0.05
    assert rows[0]["outcome_1m"] == 0.10
