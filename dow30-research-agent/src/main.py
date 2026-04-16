"""Daily pipeline entry point."""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from .analyzer import analyze_gainers
from .config import get_settings
from .fetcher import fetch_all_markets, get_past_trading_date
from .logging_setup import get_logger
from .models import DailyReport, NarrativeInsight
from .narrative import synthesize_narrative
from .notifier import send_report
from .ranker import rank_top_gainers
from .storage import (
    load_recent_reports,
    load_snapshot_by_date,
    prune_old_snapshots,
    write_report,
    write_snapshot,
)

log = get_logger(__name__)


def run(dry_run: bool = False, skip_telegram: bool = False) -> DailyReport:
    settings = get_settings()
    log.info("=== dow30-research-agent run (dry_run=%s) ===", dry_run)

    if not dry_run and not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for full runs (use --dry-run to skip)")

    stocks, trading_date = fetch_all_markets()
    if not stocks:
        raise RuntimeError("no stocks returned from yfinance")
    log.info("fetched %d stocks for %s", len(stocks), trading_date)

    write_snapshot(stocks, trading_date)

    try:
        prior_date = get_past_trading_date(settings.lookback_trading_days)
        prior = load_snapshot_by_date(prior_date)
    except RuntimeError:
        log.warning("could not determine prior trading date; cold-start mode")
        prior = None

    gainers = rank_top_gainers(stocks, prior)

    if dry_run:
        for g in gainers:
            log.info("DRY %s %s +%.2f%% sector=%s", g.ticker, g.name, g.change_pct_nd, g.sector)
        return DailyReport(
            date=trading_date, generated_at=datetime.now(UTC),
            gainers=gainers, analyses=[],
            narrative=NarrativeInsight(
                current_narrative="(dry-run)", hot_sectors=[], cooling_sectors=[],
                week_over_week_change="", investment_insight=""),
        )

    # Load yesterday's narrative for context
    prior_narrative = ""
    recent = load_recent_reports(days=1)
    if recent:
        prior_narrative = recent[0].narrative.current_narrative
    analyses = analyze_gainers(gainers, prior_narrative=prior_narrative)
    prior_reports = load_recent_reports(days=settings.narrative_lookback_days)
    narrative = synthesize_narrative(analyses, prior_reports)

    report = DailyReport(
        date=trading_date, generated_at=datetime.now(UTC),
        gainers=gainers, analyses=analyses, narrative=narrative,
    )
    write_report(report)

    if not skip_telegram:
        try:
            send_report(report)
        except Exception as exc:  # noqa: BLE001
            log.error("telegram send failed: %s", exc)

    prune_old_snapshots()
    log.info("=== run complete: %s ===", report.date)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Dow Jones 30 Research Agent daily run")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-telegram", action="store_true")
    args = parser.parse_args()
    try:
        run(dry_run=args.dry_run, skip_telegram=args.skip_telegram)
    except Exception as exc:  # noqa: BLE001
        log.exception("pipeline failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
