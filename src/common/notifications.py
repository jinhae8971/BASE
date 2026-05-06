"""Slack + Telegram + log notifications.

All sends are *fire-and-forget* and *fail-soft* — we never raise on a network
hiccup, because a missed alert is better than a halted trading loop. If the
relevant env var is empty, the channel is silently skipped.
"""
from __future__ import annotations

from typing import Any

import httpx

from .config import get_env
from .logging import get_logger

log = get_logger(__name__)


def notify(
    title: str,
    message: str,
    *,
    level: str = "info",
    fields: dict[str, Any] | None = None,
) -> None:
    """Broadcast to all configured channels."""
    log_method = getattr(log, level if level in ("info", "warning", "error") else "info")
    log_method("notify", title=title, message=message, **(fields or {}))
    _send_slack(title, message, level=level, fields=fields)
    _send_telegram(title, message, level=level, fields=fields)


def _send_slack(
    title: str,
    message: str,
    *,
    level: str,
    fields: dict[str, Any] | None,
) -> None:
    url = get_env().slack_webhook_url
    if not url:
        return
    color = {
        "error": "#dc2626",
        "warning": "#f59e0b",
        "info": "#22c55e",
    }.get(level, "#6b7280")
    attachment_fields = [
        {"title": k, "value": str(v), "short": True}
        for k, v in (fields or {}).items()
    ]
    payload = {
        "attachments": [
            {
                "color": color,
                "title": f"[MAI-System] {title}",
                "text": message,
                "fields": attachment_fields,
                "ts": _now_ts(),
            }
        ]
    }
    try:
        r = httpx.post(url, json=payload, timeout=5)
        r.raise_for_status()
    except Exception as e:
        log.warning("notify.slack_failed", error=str(e))


def _send_telegram(
    title: str,
    message: str,
    *,
    level: str,
    fields: dict[str, Any] | None,
) -> None:
    env = get_env()
    if not env.telegram_bot_token or not env.telegram_chat_id:
        return
    icon = {"error": "[X]", "warning": "[!]", "info": "[i]"}.get(level, "[*]")
    parts = [f"{icon} *{title}*", message]
    for k, v in (fields or {}).items():
        parts.append(f"• {k}: `{v}`")
    text = "\n".join(parts)
    url = f"https://api.telegram.org/bot{env.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": env.telegram_chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    try:
        r = httpx.post(url, json=payload, timeout=5)
        r.raise_for_status()
    except Exception as e:
        log.warning("notify.telegram_failed", error=str(e))


def _now_ts() -> int:
    import time

    return int(time.time())


# Convenience aliases for common events ----------------------------------
def notify_info(title: str, message: str, **fields: Any) -> None:
    notify(title, message, level="info", fields=fields or None)


def notify_warning(title: str, message: str, **fields: Any) -> None:
    notify(title, message, level="warning", fields=fields or None)


def notify_error(title: str, message: str, **fields: Any) -> None:
    notify(title, message, level="error", fields=fields or None)
