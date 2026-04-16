"""Weekly narrative synthesis over recent daily reports."""
from __future__ import annotations

import json

from .analyzer import _call_claude, _extract_json
from .config import get_settings
from .logging_setup import get_logger
from .models import CoinAnalysis, DailyReport, NarrativeInsight

log = get_logger(__name__)


def _load_system_prompt() -> str:
    settings = get_settings()
    return (settings.prompts_dir / "narrative_system.md").read_text(encoding="utf-8")


def _summarize_report(report: DailyReport) -> dict:
    return {
        "date": report.date,
        "gainers": [
            {
                "symbol": g.symbol.upper(),
                "change_48h_pct": round(g.change_48h_pct, 2),
            }
            for g in report.gainers
        ],
        "analyses": [
            {
                "symbol": a.symbol,
                "pump_thesis": a.pump_thesis,
                "category_tags": a.category_tags,
                "confidence": a.confidence,
            }
            for a in report.analyses
        ],
    }


def synthesize_narrative(
    today_analyses: list[CoinAnalysis],
    prior_reports: list[DailyReport],
) -> NarrativeInsight:
    """Combine today's analyses + recent reports into a narrative insight."""
    today_summary = {
        "date": "today",
        "analyses": [
            {
                "symbol": a.symbol,
                "pump_thesis": a.pump_thesis,
                "category_tags": a.category_tags,
                "confidence": a.confidence,
            }
            for a in today_analyses
        ],
    }
    history = [_summarize_report(r) for r in prior_reports]

    user_text = (
        "Here is the recent daily-report history (most recent first) followed "
        "by today's analyses. Produce the narrative JSON per the schema.\n\n"
        + json.dumps(
            {"today": today_summary, "history": history},
            ensure_ascii=False,
            indent=2,
        )
    )

    try:
        raw = _call_claude(_load_system_prompt(), user_text)
        data = _extract_json(raw)
        return NarrativeInsight(
            current_narrative=data.get("current_narrative", ""),
            hot_sectors=list(data.get("hot_sectors") or []),
            cooling_sectors=list(data.get("cooling_sectors") or []),
            week_over_week_change=data.get("week_over_week_change", ""),
            investment_insight=data.get("investment_insight", ""),
        )
    except Exception as exc:  # noqa: BLE001
        log.error("narrative synthesis failed: %s", exc)
        return NarrativeInsight(
            current_narrative="Narrative synthesis unavailable for today's run.",
            hot_sectors=[],
            cooling_sectors=[],
            week_over_week_change="",
            investment_insight=f"Synthesis error: {exc}",
        )
