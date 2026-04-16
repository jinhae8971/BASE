"""Daily pipeline entry point.

Flow:
  1. Fetch S&P 500 constituents + market data from yfinance
  2. Persist today's snapshot
  3. Load snapshot from N trading days ago, compute change, pick top-K
  4. Analyze each gainer with Claude (+ yfinance news)
  5. Load last N daily reports → synthesize narrative
  6. Write today's report + update dashboard index
  7. Send Telegram notification
  8. Prune stale snapshots
"""
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
    log.info("=== sp500-research-agent run (dry_run=%s) ===", dry_run)

    if not dry_run and not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for full runs (use --dry-run to skip)")

    # 1. Fetch
    stocks, trading_date = fetch_all_markets()
    if not stocks:
        raise RuntimeError("no stocks returned from yfinance")
    log.info("fetched %d stocks for trading date %s", len(stocks), trading_date)

    # 2. Snapshot
    write_snapshot(stocks, trading_date)

    # 3. Rank
    try:
        prior_date = get_past_trading_date(settings.lookback_trading_days)
        prior = load_snapshot_by_date(prior_date)
    except RuntimeError:
        log.warning("could not determine prior trading date; cold-start mode")
        prior = None

    gainers = rank_top_gainers(stocks, prior)
    if not gainers:
        log.warning("no gainers selected")

    if dry_run:
        for g in gainers:
            log.info(
                "DRY %s %s +%.2f%% sector=%s",
                g.ticker, g.name, g.change_pct_nd, g.sector,
            )
        return DailyReport(
            date=trading_date,
            generated_at=datetime.now(UTC),
            gainers=gainers,
            analyses=[],
            narrative=_empty_narrative(),
        )

    # 4. Analyze
    analyses = analyze_gainers(gainers)

    # 5. Narrative
    prior_reports = load_recent_reports(days=settings.narrative_lookback_days)
    narrative = synthesize_narrative(analyses, prior_reports)

    # 6. Build & write report
    report = DailyReport(
        date=trading_date,
        generated_at=datetime.now(UTC),
        gainers=gainers,
        analyses=analyses,
        narrative=narrative,
    )
    write_report(report)

    # 7. Notify
    if not skip_telegram:
        try:
            send_report(report)
        except Exception as exc:  # noqa: BLE001
            log.error("telegram send failed: %s", exc)

    # 8. Housekeeping
    prune_old_snapshots()

    log.info("=== run complete: %s ===", report.date)
    return report


def _empty_narrative() -> NarrativeInsight:
    return NarrativeInsight(
        current_narrative="(dry-run)",
        hot_sectors=[],
        cooling_sectors=[],
        week_over_week_change="",
        investment_insight="",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="S&P 500 Research Agent daily run")
    parser.add_argument("--dry-run", action="store_true", help="fetch + rank only, no LLM/telegram")
    parser.add_argument("--skip-telegram", action="store_true", help="skip telegram notification")
    args = parser.parse_args()
    try:
        run(dry_run=args.dry_run, skip_telegram=args.skip_telegram)
    except Exception as exc:  # noqa: BLE001
        log.exception("pipeline failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
