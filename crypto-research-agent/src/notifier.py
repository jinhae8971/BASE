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


def _escape_md(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    return "".join("\\" + c if c in MD_ESCAPE_CHARS else c for c in text or "")


def _format_message(report: DailyReport, dashboard_url: str) -> str:
    narrative = report.narrative
    lines: list[str] = []
    lines.append(f"🚀 *Crypto Daily — {_escape_md(report.date)}*")
    lines.append("")
    lines.append("*📊 Narrative*")
    lines.append(_escape_md(narrative.current_narrative))
    if narrative.hot_sectors:
        lines.append(
            "🔥 Hot: " + _escape_md(", ".join(narrative.hot_sectors))
        )
    if narrative.cooling_sectors:
        lines.append(
            "❄️ Cooling: " + _escape_md(", ".join(narrative.cooling_sectors))
        )
    lines.append("")
    lines.append("*🏆 48h Top Gainers*")
    analyses_by_symbol = {a.symbol.upper(): a for a in report.analyses}
    for i, g in enumerate(report.gainers, start=1):
        symbol = g.symbol.upper()
        analysis = analyses_by_symbol.get(symbol)
        thesis = analysis.pump_thesis if analysis else ""
        lines.append(
            f"{i}\\. *{_escape_md(symbol)}* "
            f"\\+{_escape_md(f'{g.change_48h_pct:.1f}')}%  "
            f"{_escape_md(thesis[:90])}"
        )
    lines.append("")
    lines.append("*💡 Insight*")
    lines.append(_escape_md(narrative.investment_insight))
    lines.append("")
    # Build direct link to the specific date on the dashboard
    link = dashboard_url.rstrip("/") + f"/report.html?date={report.date}"
    lines.append(f"[📈 전체 보고서 · 과거 이력 보기]({_escape_md(link)})")
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
    MAX_TG_LEN = 4096
    if len(text) > MAX_TG_LEN:
        # Truncate but keep the dashboard link at the end
        link_line = text.rsplit("\n", 1)[-1] if "\n" in text else ""
        text = text[: MAX_TG_LEN - len(link_line) - 20] + "\n\\.\\.\\.\n" + link_line
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": False,
    }
    _send(settings.telegram_bot_token, payload)
    log.info("telegram notification sent for %s", report.date)
