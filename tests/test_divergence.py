from __future__ import annotations

from datetime import date

from portfolio.divergence import append_paper_nav, evaluate_divergence
from portfolio.risk_guards import persist_nav


def _patch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    from common import config as c

    c.get_env.cache_clear()
    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {"monitor": {"divergence_threshold": 0.005}},
    )


def test_divergence_skipped_when_curves_missing(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    out = evaluate_divergence(date(2025, 5, 6))
    assert out["breached"] is False
    assert "_skipped" in out


def test_divergence_within_threshold_does_not_breach(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    persist_nav(date(2025, 5, 1), 100_000_000)
    persist_nav(date(2025, 5, 2), 101_000_000)  # +1%
    append_paper_nav(date(2025, 5, 1), 100_000_000)
    append_paper_nav(date(2025, 5, 2), 100_700_000)  # +0.7%, diff 0.3% (< 0.5%)
    out = evaluate_divergence(date(2025, 5, 2))
    assert out["breached"] is False


def test_divergence_breached_triggers_alert(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    persist_nav(date(2025, 5, 1), 100_000_000)
    persist_nav(date(2025, 5, 2), 102_000_000)  # +2%
    append_paper_nav(date(2025, 5, 1), 100_000_000)
    append_paper_nav(date(2025, 5, 2), 100_000_000)  # 0%, diff 2%
    out = evaluate_divergence(date(2025, 5, 2))
    assert out["breached"] is True
    assert abs(out["diff_pct"]) > 0.005
