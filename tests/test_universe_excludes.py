"""Verify universe filter drops blacklisted tickers (settings + override file)."""
from __future__ import annotations

import json


def _patch(monkeypatch, tmp_path, settings: dict | None = None) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    from common import config as c

    c.get_env.cache_clear()
    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings", lambda: settings or {}
    )
    # Bust the universe cache too
    from data import universe as u

    u.get_universe.cache_clear()


def test_settings_exclude_tickers_dropped(monkeypatch, tmp_path) -> None:
    _patch(
        monkeypatch,
        tmp_path,
        {
            "universe": {"exclude_tickers": ["005930"]},
        },
    )
    from data.universe import get_universe

    rows = get_universe()
    tickers = {r["ticker"] for r in rows}
    assert "005930" not in tickers
    # Some other ticker should still be present
    assert "000660" in tickers


def test_runtime_override_file_excludes(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    # Drop a runtime override file — operator emergency control
    (tmp_path / "excluded_tickers.json").write_text(
        json.dumps(["005930", "000660"]), encoding="utf-8"
    )
    from data.universe import get_universe

    rows = get_universe()
    tickers = {r["ticker"] for r in rows}
    assert "005930" not in tickers
    assert "000660" not in tickers


def test_no_excludes_keeps_full_universe(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    from data.universe import get_universe

    rows = get_universe()
    tickers = {r["ticker"] for r in rows}
    assert "005930" in tickers
