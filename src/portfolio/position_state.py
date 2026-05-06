"""Per-ticker entry / peak tracking for asymmetric stops.

We persist (entry_price, entry_date, peak_price, peak_date) for every name
the system has bought, so trailing-take-profit and hard stop-loss have a
reference. The journal already records every fill, but we keep this small
SQLite table for fast lookup at order time.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from common.config import get_setting


def _db_path() -> Path:
    p = Path(get_setting("memory.journal_db", "data_store/journal.sqlite"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path())
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS position_state (
            ticker TEXT PRIMARY KEY,
            entry_price REAL,
            entry_date TEXT,
            peak_price REAL,
            peak_date TEXT,
            qty INTEGER
        )
        """
    )
    return c


@dataclass
class PositionState:
    ticker: str
    entry_price: float
    entry_date: date
    peak_price: float
    peak_date: date
    qty: int

    def trailing_drop_pct(self, current_price: float) -> float:
        if self.peak_price <= 0:
            return 0.0
        return current_price / self.peak_price - 1.0

    def total_pnl_pct(self, current_price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        return current_price / self.entry_price - 1.0


def upsert_on_buy(ticker: str, fill_price: float, fill_qty: int, when: date) -> None:
    """Update entry/peak after a BUY fill.

    - First buy on a flat position: set entry = fill_price, peak = fill_price.
    - Adding to existing: weighted-average entry, peak = max(peak, fill_price).
    """
    if fill_qty <= 0 or fill_price <= 0:
        return
    with _conn() as c:
        cur = c.execute(
            "SELECT entry_price, peak_price, qty FROM position_state WHERE ticker = ?",
            (ticker,),
        )
        row = cur.fetchone()
        if row is None:
            c.execute(
                "INSERT INTO position_state (ticker, entry_price, entry_date, "
                "peak_price, peak_date, qty) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    fill_price,
                    when.isoformat(),
                    fill_price,
                    when.isoformat(),
                    fill_qty,
                ),
            )
            return
        old_entry, old_peak, old_qty = float(row[0] or 0), float(row[1] or 0), int(row[2] or 0)
        new_qty = old_qty + fill_qty
        new_entry = (old_entry * old_qty + fill_price * fill_qty) / max(new_qty, 1)
        new_peak = max(old_peak, fill_price)
        c.execute(
            "UPDATE position_state SET entry_price = ?, peak_price = ?, "
            "peak_date = ?, qty = ? WHERE ticker = ?",
            (new_entry, new_peak, when.isoformat(), new_qty, ticker),
        )


def update_peak(ticker: str, current_price: float, today: date) -> None:
    if current_price <= 0:
        return
    with _conn() as c:
        cur = c.execute(
            "SELECT peak_price FROM position_state WHERE ticker = ?", (ticker,)
        )
        row = cur.fetchone()
        if row is None:
            return
        old_peak = float(row[0] or 0)
        if current_price > old_peak:
            c.execute(
                "UPDATE position_state SET peak_price = ?, peak_date = ? "
                "WHERE ticker = ?",
                (current_price, today.isoformat(), ticker),
            )


def remove(ticker: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM position_state WHERE ticker = ?", (ticker,))


def get(ticker: str) -> PositionState | None:
    with _conn() as c:
        cur = c.execute(
            "SELECT ticker, entry_price, entry_date, peak_price, peak_date, qty "
            "FROM position_state WHERE ticker = ?",
            (ticker,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return PositionState(
        ticker=row[0],
        entry_price=float(row[1] or 0),
        entry_date=date.fromisoformat(row[2]) if row[2] else date.today(),
        peak_price=float(row[3] or 0),
        peak_date=date.fromisoformat(row[4]) if row[4] else date.today(),
        qty=int(row[5] or 0),
    )


def all_states() -> list[PositionState]:
    with _conn() as c:
        cur = c.execute(
            "SELECT ticker, entry_price, entry_date, peak_price, peak_date, qty "
            "FROM position_state"
        )
        rows = cur.fetchall()
    out: list[PositionState] = []
    for r in rows:
        out.append(
            PositionState(
                ticker=r[0],
                entry_price=float(r[1] or 0),
                entry_date=date.fromisoformat(r[2]) if r[2] else date.today(),
                peak_price=float(r[3] or 0),
                peak_date=date.fromisoformat(r[4]) if r[4] else date.today(),
                qty=int(r[5] or 0),
            )
        )
    return out
