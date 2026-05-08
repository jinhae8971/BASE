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
            c.execute("CREATE INDEX IF NOT EXISTS idx_decisions_ticker ON decisions(ticker)")
            # Online migration: add prompt_version on existing DBs.
            cur = c.execute("PRAGMA table_info(decisions)")
            cols = {row[1] for row in cur.fetchall()}
            if "prompt_version" not in cols:
                c.execute("ALTER TABLE decisions ADD COLUMN prompt_version TEXT")

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
        prompt_version: str | None = None,
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
                   (id, ts, agent, action, ticker, conviction, rationale, context_json, prompt_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec.id,
                    rec.timestamp.isoformat(),
                    rec.agent_name,
                    rec.action,
                    rec.ticker,
                    rec.conviction,
                    rec.rationale,
                    json.dumps(rec.context, ensure_ascii=False, default=str),
                    prompt_version,
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
            return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    def find_by_ticker(self, ticker: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            cur = c.execute(
                "SELECT * FROM decisions WHERE ticker = ? ORDER BY ts DESC LIMIT ?",
                (ticker, limit),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    def update_outcome(
        self,
        decision_id: str,
        *,
        outcome_1w: float | None = None,
        outcome_1m: float | None = None,
        outcome_3m: float | None = None,
    ) -> None:
        sets: list[str] = []
        vals: list[Any] = []
        if outcome_1w is not None:
            sets.append("outcome_1w = ?")
            vals.append(outcome_1w)
        if outcome_1m is not None:
            sets.append("outcome_1m = ?")
            vals.append(outcome_1m)
        if outcome_3m is not None:
            sets.append("outcome_3m = ?")
            vals.append(outcome_3m)
        if not sets:
            return
        vals.append(decision_id)
        with self._conn() as c:
            c.execute(f"UPDATE decisions SET {', '.join(sets)} WHERE id = ?", vals)

    def pending_outcomes(self, horizon: str = "1w") -> list[dict[str, Any]]:
        """Decisions whose outcome window has elapsed but is not yet filled."""
        days = {"1w": 7, "1m": 30, "3m": 90}.get(horizon, 7)
        col = f"outcome_{horizon}"
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._conn() as c:
            cur = c.execute(
                f"SELECT * FROM decisions WHERE ticker IS NOT NULL "
                f"AND ts <= ? AND {col} IS NULL ORDER BY ts ASC",
                (cutoff,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def summarize_decisions(as_of: date, lookback_days: int = 30) -> dict[str, Any]:
    """Compact summary for the ReflectionAgent prompt."""
    j = DecisionJournal()
    rows = j.recent(lookback_days)
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_agent.setdefault(r["agent"], []).append(r)

    # Outcome attribution per agent
    attribution: dict[str, dict[str, Any]] = {}
    for agent, xs in by_agent.items():
        with_out = [x for x in xs if x.get("outcome_1m") is not None]
        avg_1m = (
            sum(x["outcome_1m"] for x in with_out) / len(with_out) if with_out else None
        )
        hit = sum(1 for x in with_out if (x.get("outcome_1m") or 0) > 0)
        attribution[agent] = {
            "n": len(xs),
            "n_with_outcome": len(with_out),
            "avg_1m_return": avg_1m,
            "hit_ratio_1m": hit / len(with_out) if with_out else None,
        }

    return {
        "as_of": as_of.isoformat(),
        "lookback_days": lookback_days,
        "total_decisions": len(rows),
        "by_agent": {a: len(xs) for a, xs in by_agent.items()},
        "attribution": attribution,
        "sample": rows[:30],
    }
