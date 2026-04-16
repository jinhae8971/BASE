"""Tests for snapshot and report persistence."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src import storage as storage_module
from src.config import Settings
from src.models import DailyReport, GainerStock, NarrativeInsight, StockAnalysis


@pytest.fixture(autouse=True)
def tmp_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    s = Settings(anthropic_api_key="x", telegram_bot_token="x", telegram_chat_id="x",
                 repo_root=tmp_path)
    monkeypatch.setattr("src.storage.get_settings", lambda: s)
    (tmp_path / "data" / "snapshots").mkdir(parents=True)
    (tmp_path / "docs" / "reports").mkdir(parents=True)
    yield s


def _report(date: str) -> DailyReport:
    return DailyReport(
        date=date, generated_at=datetime.now(UTC),
        gainers=[GainerStock(ticker="UNH", name="UnitedHealth", sector="Healthcare",
                             close=530, market_cap=4.9e11, trading_value=3e9, volume=5e6,
                             change_pct_1d=3.0, change_pct_nd=6.5)],
        analyses=[StockAnalysis(ticker="UNH", name="UnitedHealth",
                                pump_thesis="Earnings beat", drivers=["earnings"],
                                risks=["regulation"], sector_tags=["Healthcare"],
                                confidence=0.8)],
        narrative=NarrativeInsight(current_narrative="Defensive rotation", hot_sectors=["Healthcare"],
                                   cooling_sectors=["Technology"],
                                   week_over_week_change="shift", investment_insight="lean defensive"),
    )


def test_write_report_and_index(tmp_settings):
    storage_module.write_report(_report("2026-04-14"))
    storage_module.write_report(_report("2026-04-15"))
    index = json.loads((tmp_settings.reports_dir / "index.json").read_text())
    assert index[0]["date"] == "2026-04-15"
    assert index[0]["top5"][0]["ticker"] == "UNH"


def test_index_dedup(tmp_settings):
    storage_module.write_report(_report("2026-04-15"))
    storage_module.write_report(_report("2026-04-15"))
    index = json.loads((tmp_settings.reports_dir / "index.json").read_text())
    assert [e["date"] for e in index].count("2026-04-15") == 1
