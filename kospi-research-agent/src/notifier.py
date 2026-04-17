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
    """Escape text for Telegram MarkdownV2."""
    return "".join("\\" + c if c in MD_ESCAPE_CHARS else c for c in text or "")


def _format_message(report: DailyReport, dashboard_url: str) -> str:
    n = report.narrative
    lines: list[str] = []
    lines.append(f"📈 *KOSPI 데일리* ┃ {_esc(report.date)}")
    lines.append("")
    lines.append(f"📊 *{_esc(n.current_narrative)}*")
    sectors: list[str] = []
    if n.hot_sectors:
        sectors.append("🔥 " + _esc(", ".join(n.hot_sectors)))
    if n.cooling_sectors:
        sectors.append("❄️ " + _esc(", ".join(n.cooling_sectors)))
    if sectors:
        lines.append(" ┃ ".join(sectors))
    lines.append("")
    analyses_by_ticker = {a.ticker: a for a in report.analyses}
    for i, g in enumerate(report.gainers, start=1):
        a = analyses_by_ticker.get(g.ticker)
        thesis = a.pump_thesis if a else ""
        conf = a.confidence if a else 0
        warn = " ⚠️" if conf < 0.3 else ""
        pct = f"+{g.change_pct_nd:.1f}%"
        lines.append(
            f"{i}\\. *{_esc(g.name)}* {_esc(g.ticker)} {_esc(pct)}{_esc(warn)}  {_esc(thesis[:70])}"
        )
    lines.append("")
    if n.investment_insight:
        lines.append(f"💡 {_esc(n.investment_insight)}")
        lines.append("")
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
