"""Daily pipeline entry point.

Flow:
  1. Fetch all stocks for configured market(s) from KRX via pykrx
  2. Persist today's snapshot (trading-date based)
  3. Load the snapshot from N trading days ago, compute change, pick top-K
  4. Analyze each gainer with Claude (+ Naver Finance news)
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
    if not dry_run and not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for full runs (use --dry-run to skip)")
    log.info("=== kospi-research-agent run (dry_run=%s, market=%s) ===", dry_run, settings.market)

    # 1. Fetch
    stocks, trading_date = fetch_all_markets()
    if not stocks:
        raise RuntimeError("no stocks returned from KRX")
    log.info("fetched %d stocks for trading date %s", len(stocks), trading_date)

    # Normalise trading_date to YYYY-MM-DD for report
    td = trading_date.replace("-", "")
    date_label = f"{td[:4]}-{td[4:6]}-{td[6:8]}"

    # 2. Snapshot
    write_snapshot(stocks, trading_date)

    # 3. Rank (using N-trading-days-ago snapshot)
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
                "DRY %s %s +%.2f%% mcap_rank=%s",
                g.ticker, g.name, g.change_pct_nd, g.market_cap_rank,
            )
        return DailyReport(
            date=date_label,
            generated_at=datetime.now(UTC),
            market_type=settings.market,
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
        date=date_label,
        generated_at=datetime.now(UTC),
        market_type=settings.market,
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
    parser = argparse.ArgumentParser(description="KOSPI Research Agent daily run")
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
