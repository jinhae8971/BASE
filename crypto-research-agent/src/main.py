"""Daily pipeline entry point.

Flow:
  1. Fetch top-500 markets from CoinGecko
  2. Persist today's snapshot
  3. Load the snapshot from 2 days ago, compute 48h change, pick top-K
  4. Analyze each gainer with Claude (+ news context)
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
from .fetcher import fetch_top_markets
from .logging_setup import get_logger
from .models import DailyReport
from .narrative import synthesize_narrative
from .notifier import send_report
from .ranker import rank_top_gainers
from .storage import (
    load_recent_reports,
    load_snapshot,
    prune_old_snapshots,
    today_utc,
    write_report,
    write_snapshot,
)

log = get_logger(__name__)


def run(dry_run: bool = False, skip_telegram: bool = False) -> DailyReport:
    settings = get_settings()
    if not dry_run and not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for full runs (use --dry-run to skip)")
    log.info("=== crypto-research-agent run (dry_run=%s) ===", dry_run)

    # 1. Fetch
    markets = fetch_top_markets(settings.top_n_coins)
    if not markets:
        raise RuntimeError("no markets returned from CoinGecko")

    # 2. Snapshot
    write_snapshot(markets)

    # 3. Rank (using 2-day-old snapshot)
    prior = load_snapshot(days_ago=2)
    gainers = rank_top_gainers(markets, prior)
    if not gainers:
        log.warning("no gainers selected (possibly cold-start with no positive 24h movers)")

    if dry_run:
        for g in gainers:
            log.info("DRY %s +%.2f%%  mc_rank=%s", g.symbol.upper(), g.change_48h_pct, g.market_cap_rank)
        return DailyReport(
            date=today_utc(),
            generated_at=datetime.now(UTC),
            gainers=gainers,
            analyses=[],
            narrative=_empty_narrative(),
        )

    # 4. Analyze
    # Load yesterday's narrative for context
    prior_narrative = ""
    recent = load_recent_reports(days=1)
    if recent:
        prior_narrative = recent[0].narrative.current_narrative
    analyses = analyze_gainers(gainers, prior_narrative=prior_narrative)

    # 5. Narrative
    prior_reports = load_recent_reports(days=settings.narrative_lookback_days)
    narrative = synthesize_narrative(analyses, prior_reports)

    # 6. Build & write report
    report = DailyReport(
        date=today_utc(),
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


def _empty_narrative():
    from .models import NarrativeInsight

    return NarrativeInsight(
        current_narrative="(dry-run)",
        hot_sectors=[],
        cooling_sectors=[],
        week_over_week_change="",
        investment_insight="",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Crypto Research Agent daily run")
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
