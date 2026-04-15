"""Decision Journal — persistent SQLite record of every proposal + outcome.

Schema:
    decisions(
        id TEXT PK,
        ts TEXT,
        agent TEXT,
        action TEXT,
        ticker TEXT,
        conviction INTEGER,
        rationale TEXT,
        context_json TEXT,
        outcome_1w REAL,
        outcome_1m REAL,
        outcome_3m REAL
    )
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from common.config import get_setting
from common.types import DecisionRecord


class DecisionJournal:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(
            db_path or get_setting("memory.journal_db", "data_store/journal.sqlite")
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    action TEXT NOT NULL,
                    ticker TEXT,
                    conviction INTEGER,
                    rationale TEXT,
                    context_json TEXT,
                    outcome_1w REAL,
                    outcome_1m REAL,
                    outcome_3m REAL
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_decisions_agent ON decisions(agent)")

    # ------------------------------------------------------------------
    def record(
        self,
        *,
        agent: str,
        action: str,
        ticker: str | None,
        conviction: int,
        rationale: str,
        context: dict[str, Any],
    ) -> DecisionRecord:
        rec = DecisionRecord(
            id=str(uuid.uuid4()),
            timestamp=datetime.utcnow(),
            agent_name=agent,
            action=action,
            ticker=ticker,
            conviction=conviction,
            rationale=rationale,
            context=context,
        )
        with self._conn() as c:
            c.execute(
                """INSERT INTO decisions
                   (id, ts, agent, action, ticker, conviction, rationale, context_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec.id,
                    rec.timestamp.isoformat(),
                    rec.agent_name,
                    rec.action,
                    rec.ticker,
                    rec.conviction,
                    rec.rationale,
                    json.dumps(rec.context, ensure_ascii=False, default=str),
                ),
            )
        return rec

    def recent(self, lookback_days: int = 30) -> list[dict[str, Any]]:
        cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
        with self._conn() as c:
            cur = c.execute(
                "SELECT * FROM decisions WHERE ts >= ? ORDER BY ts DESC", (cutoff,)
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def summarize_decisions(as_of: date, lookback_days: int = 30) -> dict[str, Any]:
    """Compact summary for the ReflectionAgent prompt."""
    j = DecisionJournal()
    rows = j.recent(lookback_days)
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_agent.setdefault(r["agent"], []).append(r)
    return {
        "as_of": as_of.isoformat(),
        "lookback_days": lookback_days,
        "total_decisions": len(rows),
        "by_agent": {a: len(xs) for a, xs in by_agent.items()},
        "sample": rows[:20],
    }
