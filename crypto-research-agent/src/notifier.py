"""Telegram delivery of daily report summary."""
from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import get_settings
from .logging_setup import get_logger
from .models import DailyReport

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"
MD_ESCAPE_CHARS = r"_*[]()~`>#+-=|{}.!\\"


def _esc(text: str) -> str:
    return "".join("\\" + c if c in MD_ESCAPE_CHARS else c for c in text or "")


def _format_message(report: DailyReport, dashboard_url: str) -> str:
    n = report.narrative
    lines: list[str] = []

    # Header
    lines.append(f"🚀 *크립토 데일리* ┃ {_esc(report.date)}")
    lines.append("")

    # Narrative — 한 줄 압축
    lines.append(f"📊 *{_esc(n.current_narrative)}*")
    sectors: list[str] = []
    if n.hot_sectors:
        sectors.append("🔥 " + _esc(", ".join(n.hot_sectors)))
    if n.cooling_sectors:
        sectors.append("❄️ " + _esc(", ".join(n.cooling_sectors)))
    if sectors:
        lines.append(" ┃ ".join(sectors))
    lines.append("")

    # Top 5 — 압축 포맷: 순위 심볼 +%  핵심원인
    analyses_by_symbol = {a.symbol.upper(): a for a in report.analyses}
    for i, g in enumerate(report.gainers, start=1):
        sym = g.symbol.upper()
        a = analyses_by_symbol.get(sym)
        thesis = a.pump_thesis if a else ""
        conf = a.confidence if a else 0
        warn = " ⚠️" if conf < 0.3 else ""
        pct = f"+{g.change_48h_pct:.1f}%"
        lines.append(
            f"{i}\\. *{_esc(sym)}* {_esc(pct)}{_esc(warn)}  {_esc(thesis[:70])}"
        )
    lines.append("")

    # Insight — 볼드 강조
    if n.investment_insight:
        lines.append(f"💡 {_esc(n.investment_insight)}")
        lines.append("")

    # Link
    link = dashboard_url.rstrip("/") + f"/report.html?date={report.date}"
    lines.append(f"[📈 상세보고서 보기]({_esc(link)})")
    return "\n".join(lines)


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=16))
def _send(token: str, payload: dict) -> None:
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    with httpx.Client(timeout=20.0) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()


def send_report(report: DailyReport) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.warning("telegram credentials missing; skipping notification")
        return

    text = _format_message(report, settings.dashboard_url)
    if len(text) > 4096:
        link_line = text.rsplit("\n", 1)[-1] if "\n" in text else ""
        text = text[: 4096 - len(link_line) - 20] + "\n\\.\\.\\.\n" + link_line
    _send(settings.telegram_bot_token, {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": False,
    })
    log.info("telegram notification sent for %s", report.date)
