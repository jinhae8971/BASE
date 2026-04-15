"""Snapshot archive: append-only NDJSON per source per UTC day.

Every successful raw fetch gets a line written to
`data/archive/<source>/<YYYY-MM-DD>.ndjson`. This makes backtest replay
trivial (line-by-line) and gives us an audit trail independent of the
database. NDJSON keeps us pandas-free for Phase 1; Phase 2 can batch-convert
to Parquet.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ARCHIVE_ROOT = Path("data/archive")


def _path_for(source: str, day: datetime) -> Path:
    return ARCHIVE_ROOT / source / f"{day.strftime('%Y-%m-%d')}.ndjson"


def append(source: str, payload: Any, ts: datetime | None = None) -> Path:
    now = ts or datetime.now(UTC)
    path = _path_for(source, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": now.isoformat(), "payload": payload}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
    return path
