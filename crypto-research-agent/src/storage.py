"""Filesystem persistence: daily snapshots and reports."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import get_settings
from .logging_setup import get_logger
from .models import CoinMarket, CoinSnapshot, DailyReport, DailySnapshot

log = get_logger(__name__)

SNAPSHOT_RETENTION_DAYS = 14


def today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def write_snapshot(markets: list[CoinMarket]) -> Path:
    settings = get_settings()
    settings.snapshots_dir.mkdir(parents=True, exist_ok=True)
    date = today_utc()
    snapshot = DailySnapshot(
        date=date,
        fetched_at=datetime.now(UTC),
        coins=[
            CoinSnapshot(
                id=m.id,
                symbol=m.symbol,
                price=m.current_price,
                market_cap=m.market_cap,
                volume=m.total_volume,
            )
            for m in markets
        ],
    )
    path = settings.snapshots_dir / f"{date}.json"
    path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    log.info("wrote snapshot %s (%d coins)", path.name, len(snapshot.coins))
    return path


def load_snapshot(days_ago: int) -> DailySnapshot | None:
    settings = get_settings()
    target_date = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    path = settings.snapshots_dir / f"{target_date}.json"
    if not path.exists():
        log.info("snapshot for %s not found (days_ago=%d)", target_date, days_ago)
        return None
    return DailySnapshot.model_validate_json(path.read_text(encoding="utf-8"))


def prune_old_snapshots() -> None:
    settings = get_settings()
    if not settings.snapshots_dir.exists():
        return
    cutoff = datetime.now(UTC) - timedelta(days=SNAPSHOT_RETENTION_DAYS)
    for p in settings.snapshots_dir.glob("*.json"):
        try:
            file_date = datetime.strptime(p.stem, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            continue
        if file_date < cutoff:
            p.unlink(missing_ok=True)
            log.info("pruned old snapshot %s", p.name)


def write_report(report: DailyReport) -> Path:
    settings = get_settings()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)

    # Individual report file
    report_path = settings.reports_dir / f"{report.date}.json"
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    log.info("wrote report %s", report_path.name)

    # Update index.json (dashboard listing)
    update_index(report)
    return report_path


def update_index(report: DailyReport) -> None:
    settings = get_settings()
    index_path = settings.reports_dir / "index.json"
    index: list[dict] = []
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("index.json corrupted, rebuilding")

    # remove any existing entry for today, then prepend
    index = [e for e in index if e.get("date") != report.date]
    entry = {
        "date": report.date,
        "narrative_tagline": report.narrative_tagline,
        "top5": [
            {
                "symbol": g.symbol.upper(),
                "name": g.name,
                "change_48h_pct": round(g.change_48h_pct, 2),
            }
            for g in report.gainers
        ],
    }
    index.insert(0, entry)
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("updated index.json (%d entries)", len(index))


def load_recent_reports(days: int) -> list[DailyReport]:
    """Load up to `days` most recent report JSONs (for narrative analysis)."""
    settings = get_settings()
    if not settings.reports_dir.exists():
        return []
    files = sorted(settings.reports_dir.glob("20*.json"), reverse=True)
    out: list[DailyReport] = []
    for f in files[:days]:
        try:
            out.append(DailyReport.model_validate_json(f.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            log.warning("skipping %s: %s", f.name, exc)
    return out
