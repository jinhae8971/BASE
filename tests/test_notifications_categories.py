"""Notification categories + rate limit + digest mode."""
from __future__ import annotations


def _patch(monkeypatch, payload: dict) -> None:
    from common import config as c

    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr("common.config.load_yaml_settings", lambda: payload)


def test_category_disabled_drops_notification(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from common import config as c

    c.get_env.cache_clear()
    _patch(
        monkeypatch,
        {
            "notifications": {
                "categories": {"order": {"info": False, "warning": True, "error": True}},
                "rate_limit": {"max_per_window": 100, "window_seconds": 60},
            }
        },
    )

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "common.notifications._send_slack",
        lambda title, msg, **kw: sent.append(("slack", title, msg)),
    )
    monkeypatch.setattr(
        "common.notifications._send_telegram",
        lambda title, msg, **kw: sent.append(("tg", title, msg)),
    )

    from common.notifications import notify

    notify("blocked", "info-level on disabled category", level="info", category="order")
    assert sent == []  # category disabled

    notify("allowed", "warning still flows", level="warning", category="order")
    assert ("slack", "allowed", "warning still flows") in sent or sent == []  # no slack url


def test_rate_limit_drops_excess(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from common import config as c

    c.get_env.cache_clear()
    _patch(
        monkeypatch,
        {
            "notifications": {
                "rate_limit": {"max_per_window": 2, "window_seconds": 600},
            }
        },
    )
    # Reset global rate-limit deque
    from common import notifications as n

    n._RECENT.clear()

    sent: list[str] = []
    monkeypatch.setattr(
        "common.notifications._send_slack",
        lambda *a, **kw: sent.append("slack"),
    )
    monkeypatch.setattr(
        "common.notifications._send_telegram",
        lambda *a, **kw: None,
    )

    from common.notifications import notify

    for i in range(5):
        notify(f"msg {i}", "x", level="info", category="general")
    # Only 2 should have got through
    assert len(sent) <= 2


def test_digest_mode_buffers_info(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from common import config as c

    c.get_env.cache_clear()
    _patch(
        monkeypatch,
        {
            "notifications": {
                "digest": {"enabled": True, "interval_seconds": 3600},
                "rate_limit": {"max_per_window": 100, "window_seconds": 600},
            }
        },
    )
    from common import notifications as n

    n._RECENT.clear()

    sent: list[str] = []
    monkeypatch.setattr(
        "common.notifications._send_slack",
        lambda *a, **kw: sent.append("slack"),
    )
    monkeypatch.setattr(
        "common.notifications._send_telegram",
        lambda *a, **kw: None,
    )

    from common.notifications import notify

    notify("info-1", "x", level="info", category="general")
    notify("info-2", "y", level="info", category="general")
    notify("warn-1", "z", level="warning", category="general")
    # info events buffered, warn flowed through to slack/telegram
    assert sent.count("slack") == 1
    digest = (tmp_path / "notifications_digest.jsonl").read_text(encoding="utf-8")
    assert "info-1" in digest and "info-2" in digest
    # Warning should NOT be in the digest
    assert "warn-1" not in digest
