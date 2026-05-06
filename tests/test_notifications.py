from __future__ import annotations

from common.notifications import notify, notify_error, notify_info, notify_warning


def test_notify_no_channels_does_not_raise(monkeypatch) -> None:
    """With no SLACK_WEBHOOK_URL or TELEGRAM_BOT_TOKEN we must not raise."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    # Reset the cached env
    from common import config as c

    c.get_env.cache_clear()
    notify_info("hello", "world")
    notify_warning("warn", "be careful", k=1)
    notify_error("oops", "bad", reason="x")
    notify("custom", "msg", level="info")


def test_notify_slack_failure_swallowed(monkeypatch) -> None:
    """An invalid webhook should never escape the function."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://127.0.0.1:1/never")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from common import config as c

    c.get_env.cache_clear()
    notify_error("net error path", "swallowed")
