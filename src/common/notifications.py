"""Slack + Telegram + log notifications with category gates and digest mode.

Configurable in ``settings.yaml::notifications``::

    notifications:
      categories:
        order: {info: true,  warning: true, error: true}
        risk:  {info: true,  warning: true, error: true}
        agent: {info: false, warning: true, error: true}
        booster: {info: false, warning: true, error: true}
      # Digest: when true, info-level messages are queued to a JSONL file
      # and flushed to Slack/Telegram every ``digest_interval_seconds`` as
      # a single summary message instead of one ping per event.
      digest:
        enabled: false
        interval_seconds: 3600
      rate_limit:
        # Max messages of the same (category,level) within window_seconds
        max_per_window: 10
        window_seconds: 600

All sends remain *fire-and-forget* and *fail-soft*.
"""
from __future__ import annotations

import contextlib
import json
import time
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Any

import httpx

from .config import get_env, get_setting
from .logging import get_logger

log = get_logger(__name__)


# In-process rate limiter: (category, level) -> deque of recent timestamps
_RATE_LOCK = Lock()
_RECENT: dict[tuple[str, str], deque] = {}


def _allowed_by_rate_limit(category: str, level: str) -> bool:
    cap = int(get_setting("notifications.rate_limit.max_per_window", 10) or 0)
    window = int(get_setting("notifications.rate_limit.window_seconds", 600) or 0)
    if cap <= 0 or window <= 0:
        return True
    key = (category, level)
    now = time.monotonic()
    with _RATE_LOCK:
        dq = _RECENT.setdefault(key, deque())
        while dq and (now - dq[0]) > window:
            dq.popleft()
        if len(dq) >= cap:
            return False
        dq.append(now)
    return True


def _category_enabled(category: str, level: str) -> bool:
    cats = get_setting("notifications.categories", None) or {}
    cfg = cats.get(category) if isinstance(cats, dict) else None
    if not isinstance(cfg, dict):
        return True
    val = cfg.get(level, True)
    return bool(val)


def _digest_path() -> Path:
    return Path(get_env().mais_data_dir) / "notifications_digest.jsonl"


def _digest_enabled() -> bool:
    return bool(get_setting("notifications.digest.enabled", False))


def _digest_append(payload: dict[str, Any]) -> None:
    p = _digest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception), p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def flush_digest() -> int:
    """Send everything in the digest buffer as a single summary. Called by
    a cron job (default 1h). Returns the number of items flushed."""
    p = _digest_path()
    if not p.exists():
        return 0
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        log.warning("digest.read_failed", error=str(e))
        return 0
    if not lines:
        return 0

    items = []
    for ln in lines:
        try:
            items.append(json.loads(ln))
        except Exception:
            continue
    if not items:
        with contextlib.suppress(Exception):
            p.unlink()
        return 0

    summary = "\n".join(
        f"• [{x.get('category', '?')}] {x.get('title', '')}: {x.get('message', '')[:120]}"
        for x in items[:30]
    )
    extra = f"\n…and {len(items) - 30} more" if len(items) > 30 else ""
    _send_slack(
        f"Hourly digest — {len(items)} events",
        summary + extra,
        level="info",
        fields=None,
    )
    _send_telegram(
        f"Hourly digest — {len(items)} events",
        summary + extra,
        level="info",
        fields=None,
    )
    with contextlib.suppress(Exception):
        p.unlink()
    log.info("digest.flushed", n=len(items))
    return len(items)


def notify(
    title: str,
    message: str,
    *,
    level: str = "info",
    fields: dict[str, Any] | None = None,
    category: str = "general",
) -> None:
    """Broadcast to all configured channels.

    Pipeline:
        1. Always log to structlog.
        2. If (category, level) is disabled in settings, stop.
        3. If rate-limited, drop with a debug log.
        4. If digest mode is on AND level is info, queue instead of send.
        5. Otherwise broadcast immediately.
    """
    log_method = getattr(log, level if level in ("info", "warning", "error") else "info")
    log_method("notify", title=title, message=message, category=category, **(fields or {}))

    if not _category_enabled(category, level):
        return
    if not _allowed_by_rate_limit(category, level):
        log.debug("notify.rate_limited", category=category, level=level, title=title)
        return

    if _digest_enabled() and level == "info":
        _digest_append(
            {
                "ts": time.time(),
                "title": title,
                "message": message,
                "level": level,
                "category": category,
                "fields": fields or {},
            }
        )
        return

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
    dash_url = str(get_setting("notifications.dashboard_url", "") or "").strip()
    body_text = message
    if dash_url:
        body_text = f"{message}\n\n<{dash_url}|대시보드 열기>"
    payload = {
        "attachments": [
            {
                "color": color,
                "title": f"[MAI-System] {title}",
                "text": body_text,
                "fields": attachment_fields,
                "ts": int(time.time()),
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
    dash_url = str(get_setting("notifications.dashboard_url", "") or "").strip()
    if dash_url:
        parts.append(f"[대시보드]({dash_url})")
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


# Convenience aliases for common events ----------------------------------
def notify_info(title: str, message: str, *, category: str = "general", **fields: Any) -> None:
    notify(title, message, level="info", fields=fields or None, category=category)


def notify_warning(title: str, message: str, *, category: str = "general", **fields: Any) -> None:
    notify(title, message, level="warning", fields=fields or None, category=category)


def notify_error(title: str, message: str, *, category: str = "general", **fields: Any) -> None:
    notify(title, message, level="error", fields=fields or None, category=category)
