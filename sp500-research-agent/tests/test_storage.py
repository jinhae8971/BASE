"""Tests for snapshot and report persistence."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src import storage as storage_module
from src.config import Settings
from src.models import (
    DailyReport,
    GainerStock,
    NarrativeInsight,
    StockAnalysis,
)


@pytest.fixture(autouse=True)
def tmp_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    s = Settings(
        anthropic_api_key="x",
        telegram_bot_token="x",
        telegram_chat_id="x",
        repo_root=tmp_path,
    )
    monkeypatch.setattr("src.storage.get_settings", lambda: s)
    (tmp_path / "data" / "snapshots").mkdir(parents=True)
    (tmp_path / "docs" / "reports").mkdir(parents=True)
    yield s


def _make_report(date: str) -> DailyReport:
    return DailyReport(
        date=date,
        generated_at=datetime.now(UTC),
        gainers=[
            GainerStock(
                ticker="NVDA",
                name="NVIDIA Corporation",
                sector="Technology",
                close=950.0,
                market_cap=2.3e12,
                trading_value=4e10,
                volume=40_000_000,
                change_pct_1d=5.0,
                change_pct_nd=12.3,
            )
        ],
        analyses=[
            StockAnalysis(
                ticker="NVDA",
                name="NVIDIA Corporation",
                pump_thesis="AI capex acceleration + Blackwell ramp",
                drivers=["Blackwell ramp"],
                risks=["valuation"],
                sector_tags=["Technology"],
                confidence=0.85,
            )
        ],
        narrative=NarrativeInsight(
            current_narrative="AI infrastructure spending accelerating",
            hot_sectors=["Technology"],
            cooling_sectors=["Utilities"],
            week_over_week_change="broadening beyond mega-cap",
            investment_insight="overweight AI infrastructure picks",
        ),
    )


def test_write_report_and_update_index(tmp_settings):
    r1 = _make_report("2026-04-14")
    r2 = _make_report("2026-04-15")
    storage_module.write_report(r1)
    storage_module.write_report(r2)

    index_path = tmp_settings.reports_dir / "index.json"
    assert index_path.exists()
    index = json.loads(index_path.read_text())
    assert index[0]["date"] == "2026-04-15"
    assert index[1]["date"] == "2026-04-14"
    assert index[0]["top5"][0]["ticker"] == "NVDA"


def test_update_index_deduplicates_same_date(tmp_settings):
    r1 = _make_report("2026-04-15")
    storage_module.write_report(r1)
    r1b = _make_report("2026-04-15")
    storage_module.write_report(r1b)
    index = json.loads((tmp_settings.reports_dir / "index.json").read_text())
    dates = [e["date"] for e in index]
    assert dates.count("2026-04-15") == 1
