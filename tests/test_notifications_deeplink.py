"""Notification deeplink — dashboard_url appended to Slack/Telegram messages."""
from __future__ import annotations


def test_slack_message_includes_dashboard_url(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://127.0.0.1:1/never")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from common import config as c

    c.get_env.cache_clear()
    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {
            "notifications": {
                "dashboard_url": "http://my-dash:8501",
                "rate_limit": {"max_per_window": 100, "window_seconds": 600},
            }
        },
    )

    sent: list[dict] = []

    def fake_post(url, json, timeout):
        sent.append(json)

        class R:
            def raise_for_status(self): pass

        return R()

    monkeypatch.setattr("common.notifications.httpx.post", fake_post)

    from common import notifications as n

    n._RECENT.clear()
    n.notify("test", "hello", level="info", category="general")
    assert sent
    txt = sent[0]["attachments"][0]["text"]
    assert "my-dash:8501" in txt
    assert "대시보드 열기" in txt or "Open" in txt or "<http" in txt


def test_no_dashboard_url_omits_link(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://127.0.0.1:1/never")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from common import config as c

    c.get_env.cache_clear()
    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {
            "notifications": {
                "dashboard_url": "",
                "rate_limit": {"max_per_window": 100, "window_seconds": 600},
            }
        },
    )

    sent: list[dict] = []

    def fake_post(url, json, timeout):
        sent.append(json)

        class R:
            def raise_for_status(self): pass

        return R()

    monkeypatch.setattr("common.notifications.httpx.post", fake_post)

    from common import notifications as n

    n._RECENT.clear()
    n.notify("test", "no link", level="info", category="general")
    assert sent
    txt = sent[0]["attachments"][0]["text"]
    assert "대시보드 열기" not in txt
