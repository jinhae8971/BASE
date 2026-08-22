"""SQLite persistence for the Upbit subsystem.

One file, one user — `data_store/upbit.sqlite`. Everything the dashboard shows
(포트폴리오 · 거래내역 · 분석내역 · 전략 · 장기보유) is read back from here.

Tables
    runs                 one row per scan/monitor/liquidation cycle
    analyses             every scored candidate of every run (분석내역)
    trades               every order the engine placed, paper or live
    positions            engine-managed open/closed day-trade positions
    equity_snapshots     equity curve samples
    app_settings         dashboard overrides on top of config/settings.yaml
    long_term_holdings   coins the engine must never buy or sell
    event_log            operational log surfaced in the dashboard
"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from common.config import PROJECT_ROOT, get_setting
from common.logging import get_logger

from .types import LongTermHolding, Position

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,              -- selection | monitor | liquidate | manual
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    mode         TEXT NOT NULL DEFAULT 'paper',
    regime       TEXT,
    scanned      INTEGER DEFAULT 0,
    selected     INTEGER DEFAULT 0,
    note         TEXT DEFAULT '',
    payload_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);

CREATE TABLE IF NOT EXISTS analyses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    as_of         TEXT NOT NULL,
    market        TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    korean_name   TEXT,
    rank          INTEGER,
    total_score   REAL NOT NULL,
    volume_score  REAL, flow_score REAL, tech_score REAL, beta_score REAL,
    price         REAL, change_rate_24h REAL, trade_price_24h REAL,
    selected      INTEGER NOT NULL DEFAULT 0,
    reason        TEXT DEFAULT '',
    metrics_json  TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_analyses_run ON analyses(run_id);
CREATE INDEX IF NOT EXISTS idx_analyses_market ON analyses(market, as_of DESC);

CREATE TABLE IF NOT EXISTS trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    run_id         INTEGER,
    mode           TEXT NOT NULL,
    market         TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    side           TEXT NOT NULL,             -- bid | ask
    ord_type       TEXT NOT NULL,
    volume         REAL NOT NULL DEFAULT 0,
    price          REAL NOT NULL DEFAULT 0,
    krw_amount     REAL NOT NULL DEFAULT 0,
    fee            REAL NOT NULL DEFAULT 0,
    state          TEXT NOT NULL,
    uuid           TEXT,
    reason         TEXT DEFAULT '',
    score          REAL,
    pnl            REAL,
    pnl_pct        REAL,
    entry_trade_id INTEGER,
    message        TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts DESC);
CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market);

CREATE TABLE IF NOT EXISTS positions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    market         TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    volume         REAL NOT NULL,
    avg_price      REAL NOT NULL,
    opened_at      TEXT NOT NULL,
    high_water     REAL NOT NULL DEFAULT 0,
    stop_price     REAL NOT NULL DEFAULT 0,
    take_price     REAL NOT NULL DEFAULT 0,
    mode           TEXT NOT NULL DEFAULT 'paper',
    run_id         INTEGER,
    entry_trade_id INTEGER,
    entry_score    REAL,
    status         TEXT NOT NULL DEFAULT 'open',
    closed_at      TEXT,
    exit_reason    TEXT,
    realized_pnl   REAL
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status, market);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    mode          TEXT NOT NULL,
    total_krw     REAL NOT NULL,
    cash_krw      REAL NOT NULL,
    trading_krw   REAL NOT NULL DEFAULT 0,
    longterm_krw  REAL NOT NULL DEFAULT 0,
    realized_pnl  REAL NOT NULL DEFAULT 0,
    unrealized_pnl REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_snapshots(ts DESC);

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS long_term_holdings (
    symbol          TEXT PRIMARY KEY,
    locked_quantity REAL NOT NULL DEFAULT 0,
    memo            TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    level        TEXT NOT NULL,
    category     TEXT NOT NULL,
    message      TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_event_ts ON event_log(ts DESC);
"""

_LOCK = threading.RLock()


def utc_now() -> datetime:
    """Naive UTC — the single timestamp convention for every stored value.

    ``datetime.utcnow`` is deprecated from Python 3.12; this keeps the identical
    on-disk format (no offset suffix) so the string comparisons in SQL stay valid.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _now() -> str:
    return utc_now().isoformat(timespec="seconds")


def default_db_path() -> Path:
    raw = Path(get_setting("upbit.storage.db", "data_store/upbit.sqlite"))
    return raw if raw.is_absolute() else PROJECT_ROOT / raw


class UpbitStore:
    """Thread-safe façade over the SQLite file."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------
    def start_run(self, kind: str, mode: str, note: str = "") -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO runs (kind, started_at, mode, note) VALUES (?,?,?,?)",
                (kind, _now(), mode, note),
            )
            return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str = "ok",
        regime: str | None = None,
        scanned: int = 0,
        selected: int = 0,
        note: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as c:
            c.execute(
                """UPDATE runs SET finished_at=?, status=?, regime=?, scanned=?, selected=?,
                          note=?, payload_json=? WHERE id=?""",
                (
                    _now(),
                    status,
                    regime,
                    scanned,
                    selected,
                    note,
                    json.dumps(payload or {}, ensure_ascii=False, default=str),
                    run_id,
                ),
            )

    def list_runs(self, limit: int = 60, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM runs"
        args: list[Any] = []
        if kind:
            sql += " WHERE kind=?"
            args.append(kind)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as c:
            row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Analyses
    # ------------------------------------------------------------------
    def save_analyses(self, run_id: int, as_of: str, candidates: list[Any]) -> None:
        rows = [
            (
                run_id,
                as_of,
                c.market,
                c.symbol,
                c.korean_name,
                c.rank,
                c.score.total,
                c.score.volume,
                c.score.flow,
                c.score.technical,
                c.score.beta,
                c.price,
                c.change_rate_24h,
                c.trade_price_24h,
                int(c.selected),
                c.reason,
                json.dumps(c.score.metrics, ensure_ascii=False, default=str),
            )
            for c in candidates
        ]
        with self.connect() as conn:
            conn.executemany(
                """INSERT INTO analyses
                   (run_id, as_of, market, symbol, korean_name, rank, total_score,
                    volume_score, flow_score, tech_score, beta_score, price,
                    change_rate_24h, trade_price_24h, selected, reason, metrics_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    def list_analyses(
        self,
        *,
        run_id: int | None = None,
        market: str | None = None,
        selected_only: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM analyses WHERE 1=1"
        args: list[Any] = []
        if run_id is not None:
            sql += " AND run_id=?"
            args.append(run_id)
        if market:
            sql += " AND market=?"
            args.append(market)
        if selected_only:
            sql += " AND selected=1"
        sql += " ORDER BY run_id DESC, total_score DESC LIMIT ?"
        args.append(limit)
        with self.connect() as c:
            out = []
            for r in c.execute(sql, args):
                row = dict(r)
                row["metrics"] = json.loads(row.pop("metrics_json") or "{}")
                out.append(row)
            return out

    def latest_analysis_run_id(self) -> int | None:
        with self.connect() as c:
            row = c.execute(
                "SELECT id FROM runs WHERE kind='selection' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return int(row["id"]) if row else None

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------
    # Columns declared NOT NULL DEFAULT 0: binding an explicit NULL would defeat
    # the default, so fill them in here when the caller omits them.
    _TRADE_NUMERIC_DEFAULTS: ClassVar[dict[str, float]] = {
        "volume": 0.0, "price": 0.0, "krw_amount": 0.0, "fee": 0.0,
    }

    def record_trade(self, **kw: Any) -> int:
        cols = (
            "ts", "run_id", "mode", "market", "symbol", "side", "ord_type", "volume",
            "price", "krw_amount", "fee", "state", "uuid", "reason", "score", "pnl",
            "pnl_pct", "entry_trade_id", "message",
        )
        kw.setdefault("ts", _now())
        for col, default in self._TRADE_NUMERIC_DEFAULTS.items():
            if kw.get(col) is None:
                kw[col] = default
        values = [kw.get(c) for c in cols]
        with self.connect() as c:
            cur = c.execute(
                f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                values,
            )
            return int(cur.lastrowid)

    def list_trades(
        self,
        *,
        limit: int = 300,
        market: str | None = None,
        side: str | None = None,
        since: str | None = None,
        mode: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM trades WHERE 1=1"
        args: list[Any] = []
        for col, val in (("market", market), ("side", side), ("mode", mode)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        if since:
            sql += " AND ts >= ?"
            args.append(since)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def trade_stats(self, mode: str | None = None, days: int | None = None) -> dict[str, Any]:
        """Realised performance, computed from closing (ask) trades."""
        sql = "SELECT pnl, pnl_pct, krw_amount, fee, ts, symbol FROM trades WHERE side='ask' AND pnl IS NOT NULL"
        args: list[Any] = []
        if mode:
            sql += " AND mode=?"
            args.append(mode)
        if days:
            sql += " AND ts >= ?"
            args.append((utc_now() - timedelta(days=days)).isoformat(timespec="seconds"))
        with self.connect() as c:
            rows = [dict(r) for r in c.execute(sql, args)]
            fee_row = c.execute(
                "SELECT COALESCE(SUM(fee),0) AS f FROM trades" + (" WHERE mode=?" if mode else ""),
                ([mode] if mode else []),
            ).fetchone()

        wins = [r for r in rows if (r["pnl"] or 0) > 0]
        losses = [r for r in rows if (r["pnl"] or 0) <= 0]
        gross_win = sum(r["pnl"] for r in wins)
        gross_loss = -sum(r["pnl"] for r in losses)
        best = max(rows, key=lambda r: r["pnl"], default=None)
        worst = min(rows, key=lambda r: r["pnl"], default=None)
        return {
            "closed_trades": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(rows)) if rows else 0.0,
            "total_pnl": sum(r["pnl"] or 0 for r in rows),
            "avg_pnl_pct": (sum(r["pnl_pct"] or 0 for r in rows) / len(rows)) if rows else 0.0,
            "avg_win_pct": (sum(r["pnl_pct"] or 0 for r in wins) / len(wins)) if wins else 0.0,
            "avg_loss_pct": (sum(r["pnl_pct"] or 0 for r in losses) / len(losses)) if losses else 0.0,
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
            "total_fee": float(fee_row["f"]) if fee_row else 0.0,
            "best": best,
            "worst": worst,
        }

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------
    def open_position(self, pos: Position, *, entry_trade_id: int | None, score: float) -> int:
        with self.connect() as c:
            cur = c.execute(
                """INSERT INTO positions
                   (market, symbol, volume, avg_price, opened_at, high_water, stop_price,
                    take_price, mode, run_id, entry_trade_id, entry_score, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'open')""",
                (
                    pos.market, pos.symbol, pos.volume, pos.avg_price,
                    pos.opened_at.isoformat(timespec="seconds"), pos.high_water,
                    pos.stop_price, pos.take_price, pos.mode, pos.run_id,
                    entry_trade_id, score,
                ),
            )
            return int(cur.lastrowid)

    def list_open_positions(self, mode: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM positions WHERE status='open'"
        args: list[Any] = []
        if mode:
            sql += " AND mode=?"
            args.append(mode)
        sql += " ORDER BY opened_at"
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def list_closed_positions(self, limit: int = 200, mode: str | None = None) -> list[dict]:
        sql = "SELECT * FROM positions WHERE status='closed'"
        args: list[Any] = []
        if mode:
            sql += " AND mode=?"
            args.append(mode)
        sql += " ORDER BY closed_at DESC LIMIT ?"
        args.append(limit)
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def update_position(self, position_id: int, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k}=?" for k in fields)
        with self.connect() as c:
            c.execute(
                f"UPDATE positions SET {assignments} WHERE id=?",
                [*fields.values(), position_id],
            )

    def close_position(
        self, position_id: int, *, exit_reason: str, realized_pnl: float
    ) -> None:
        self.update_position(
            position_id,
            status="closed",
            closed_at=_now(),
            exit_reason=exit_reason,
            realized_pnl=realized_pnl,
        )

    # ------------------------------------------------------------------
    # Equity
    # ------------------------------------------------------------------
    def snapshot_equity(self, **kw: Any) -> None:
        kw.setdefault("ts", _now())
        cols = (
            "ts", "mode", "total_krw", "cash_krw", "trading_krw", "longterm_krw",
            "realized_pnl", "unrealized_pnl",
        )
        with self.connect() as c:
            c.execute(
                f"INSERT INTO equity_snapshots ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [kw.get(col, 0) for col in cols],
            )

    def equity_history(self, days: int = 90, mode: str | None = None) -> list[dict[str, Any]]:
        since = (utc_now() - timedelta(days=days)).isoformat(timespec="seconds")
        sql = "SELECT * FROM equity_snapshots WHERE ts >= ?"
        args: list[Any] = [since]
        if mode:
            sql += " AND mode=?"
            args.append(mode)
        sql += " ORDER BY ts, id"
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def last_equity(self, mode: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM equity_snapshots"
        args: list[Any] = []
        if mode:
            sql += " WHERE mode=?"
            args.append(mode)
        sql += " ORDER BY ts DESC, id DESC LIMIT 1"
        with self.connect() as c:
            row = c.execute(sql, args).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Settings overrides
    # ------------------------------------------------------------------
    def get_override(self, key: str, default: Any = None) -> Any:
        with self.connect() as c:
            row = c.execute("SELECT value_json FROM app_settings WHERE key=?", (key,)).fetchone()
            return json.loads(row["value_json"]) if row else default

    def set_override(self, key: str, value: Any) -> None:
        with self.connect() as c:
            c.execute(
                """INSERT INTO app_settings (key, value_json, updated_at) VALUES (?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                                                  updated_at=excluded.updated_at""",
                (key, json.dumps(value, ensure_ascii=False, default=str), _now()),
            )

    def all_overrides(self) -> dict[str, Any]:
        with self.connect() as c:
            return {
                r["key"]: json.loads(r["value_json"])
                for r in c.execute("SELECT key, value_json FROM app_settings")
            }

    def delete_override(self, key: str) -> None:
        with self.connect() as c:
            c.execute("DELETE FROM app_settings WHERE key=?", (key,))

    # ------------------------------------------------------------------
    # Long-term holdings (the exclusion list)
    # ------------------------------------------------------------------
    def upsert_long_term(self, holding: LongTermHolding) -> None:
        with self.connect() as c:
            c.execute(
                """INSERT INTO long_term_holdings (symbol, locked_quantity, memo, created_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET locked_quantity=excluded.locked_quantity,
                                                     memo=excluded.memo""",
                (holding.symbol.upper(), holding.locked_quantity, holding.memo, _now()),
            )

    def list_long_term(self) -> list[LongTermHolding]:
        with self.connect() as c:
            return [
                LongTermHolding(
                    symbol=r["symbol"],
                    locked_quantity=r["locked_quantity"],
                    memo=r["memo"] or "",
                )
                for r in c.execute("SELECT * FROM long_term_holdings ORDER BY symbol")
            ]

    def delete_long_term(self, symbol: str) -> bool:
        with self.connect() as c:
            cur = c.execute("DELETE FROM long_term_holdings WHERE symbol=?", (symbol.upper(),))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Event log
    # ------------------------------------------------------------------
    def log_event(
        self, level: str, category: str, message: str, payload: dict[str, Any] | None = None
    ) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO event_log (ts, level, category, message, payload_json) VALUES (?,?,?,?,?)",
                (
                    _now(),
                    level,
                    category,
                    message,
                    json.dumps(payload or {}, ensure_ascii=False, default=str),
                ),
            )

    # ------------------------------------------------------------------
    # Maintenance — a system that runs for months needs this
    # ------------------------------------------------------------------
    def prune(self, *, analyses_days: int, events_days: int, snapshots_days: int) -> dict[str, int]:
        """Drop aged rows. Trades and positions are financial records: never pruned."""
        removed: dict[str, int] = {}
        with self.connect() as c:
            for table, column, days in (
                ("analyses", "as_of", analyses_days),
                ("event_log", "ts", events_days),
                ("equity_snapshots", "ts", snapshots_days),
            ):
                if days <= 0:
                    continue
                cutoff = (utc_now() - timedelta(days=days)).isoformat(timespec="seconds")
                cur = c.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))
                removed[table] = cur.rowcount or 0
            # Aged runs that left no analyses and no trades are pure noise. The
            # date guard matters: without it this would wipe today's monitor
            # cycles too, and recent operational history is exactly what you
            # need when something goes wrong.
            if events_days > 0:
                cutoff = (utc_now() - timedelta(days=events_days)).isoformat(timespec="seconds")
                cur = c.execute(
                    """DELETE FROM runs
                       WHERE started_at < ?
                         AND id NOT IN (SELECT DISTINCT run_id FROM analyses WHERE run_id IS NOT NULL)
                         AND id NOT IN (SELECT DISTINCT run_id FROM trades WHERE run_id IS NOT NULL)""",
                    (cutoff,),
                )
                removed["runs"] = cur.rowcount or 0
        return removed

    def backup(self, directory: Path | str, *, keep: int = 7) -> Path:
        """Consistent on-line copy of the DB, keeping the newest ``keep`` files.

        Uses SQLite's backup API, so it is safe while the engine is writing.
        """
        target_dir = Path(directory)
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = utc_now().strftime("%Y%m%d-%H%M%S")
        target = target_dir / f"upbit-{stamp}.sqlite"

        with self.connect() as src:
            dest = sqlite3.connect(target)
            try:
                src.backup(dest)
            finally:
                dest.close()

        existing = sorted(target_dir.glob("upbit-*.sqlite"), reverse=True)
        for stale in existing[max(keep, 1) :]:
            stale.unlink(missing_ok=True)
        return target

    def vacuum(self) -> None:
        """Reclaim space after a prune. Runs outside the usual transaction."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        try:
            conn.execute("VACUUM")
            conn.execute("PRAGMA optimize")
        finally:
            conn.close()

    def database_size_bytes(self) -> int:
        try:
            return self.db_path.stat().st_size
        except OSError:
            return 0

    def list_events(self, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM event_log"
        args: list[Any] = []
        if level:
            sql += " WHERE level=?"
            args.append(level)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args)]


_STORE: UpbitStore | None = None


def get_store() -> UpbitStore:
    """Process-wide singleton — the dashboard and scheduler share one file."""
    global _STORE
    if _STORE is None:
        _STORE = UpbitStore()
    return _STORE
