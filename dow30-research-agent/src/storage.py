"""Filesystem persistence: daily snapshots and reports."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import get_settings
from .logging_setup import get_logger
from .models import DailyReport, DailySnapshot, StockMarket, StockSnapshot

log = get_logger(__name__)

SNAPSHOT_RETENTION_DAYS = 30


def write_snapshot(stocks: list[StockMarket], trading_date: str) -> Path:
    settings = get_settings()
    settings.snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot = DailySnapshot(
        date=trading_date,
        fetched_at=datetime.now(UTC),
        stocks=[
            StockSnapshot(
                ticker=s.ticker, name=s.name, close=s.close,
                market_cap=s.market_cap, trading_value=s.trading_value,
            )
            for s in stocks
        ],
    )
    path = settings.snapshots_dir / f"{trading_date}.json"
    path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    log.info("wrote snapshot %s (%d stocks)", path.name, len(snapshot.stocks))
    return path


def load_snapshot_by_date(date_str: str) -> DailySnapshot | None:
    settings = get_settings()
    path = settings.snapshots_dir / f"{date_str}.json"
    if not path.exists():
        log.info("snapshot for %s not found", date_str)
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
    report_path = settings.reports_dir / f"{report.date}.json"
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    log.info("wrote report %s", report_path.name)
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
    index = [e for e in index if e.get("date") != report.date]
    entry = {
        "date": report.date,
        "narrative_tagline": report.narrative_tagline,
        "top5": [
            {"ticker": g.ticker, "name": g.name, "sector": g.sector,
             "change_pct_nd": round(g.change_pct_nd, 2)}
            for g in report.gainers
        ],
    }
    index.insert(0, entry)
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("updated index.json (%d entries)", len(index))


def load_recent_reports(days: int) -> list[DailyReport]:
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
