"""Weekly narrative synthesis over recent daily reports."""
from __future__ import annotations

import json

from .analyzer import _call_claude, _extract_json
from .config import get_settings
from .logging_setup import get_logger
from .models import DailyReport, NarrativeInsight, StockAnalysis

log = get_logger(__name__)


def _load_system_prompt() -> str:
    settings = get_settings()
    return (settings.prompts_dir / "narrative_system.md").read_text(encoding="utf-8")


def _summarize_report(report: DailyReport) -> dict:
    return {
        "date": report.date,
        "gainers": [
            {
                "ticker": g.ticker,
                "name": g.name,
                "change_pct_nd": round(g.change_pct_nd, 2),
            }
            for g in report.gainers
        ],
        "analyses": [
            {
                "ticker": a.ticker,
                "name": a.name,
                "pump_thesis": a.pump_thesis,
                "sector_tags": a.sector_tags,
                "confidence": a.confidence,
            }
            for a in report.analyses
        ],
    }


def synthesize_narrative(
    today_analyses: list[StockAnalysis],
    prior_reports: list[DailyReport],
) -> NarrativeInsight:
    """Combine today's analyses + recent reports into a narrative insight."""
    today_summary = {
        "date": "today",
        "analyses": [
            {
                "ticker": a.ticker,
                "name": a.name,
                "pump_thesis": a.pump_thesis,
                "sector_tags": a.sector_tags,
                "confidence": a.confidence,
            }
            for a in today_analyses
        ],
    }
    history = [_summarize_report(r) for r in prior_reports]

    user_text = (
        "최근 일별 보고서 이력(최신순)과 오늘의 분석 결과입니다. "
        "시스템 프롬프트의 JSON 스키마에 맞춰 내러티브를 반환해주세요.\n\n"
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
            current_narrative="내러티브 분석을 수행할 수 없습니다.",
            hot_sectors=[],
            cooling_sectors=[],
            week_over_week_change="",
            investment_insight=f"분석 오류: {exc}",
        )
