"""Tests for snapshot and report persistence."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src import storage as storage_module
from src.config import Settings
from src.models import (
    CoinAnalysis,
    DailyReport,
    GainerCoin,
    NarrativeInsight,
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
            GainerCoin(
                id="eth",
                symbol="eth",
                name="Ethereum",
                current_price=3000.0,
                market_cap=4e11,
                market_cap_rank=2,
                total_volume=2e10,
                change_24h_pct=5.0,
                change_48h_pct=12.0,
            )
        ],
        analyses=[
            CoinAnalysis(
                coin_id="eth",
                symbol="ETH",
                pump_thesis="ETF flows returning",
                drivers=["spot ETF inflows"],
                risks=["unlock"],
                category_tags=["L1"],
                confidence=0.8,
            )
        ],
        narrative=NarrativeInsight(
            current_narrative="L1 flows resuming",
            hot_sectors=["L1"],
            cooling_sectors=["Meme"],
            week_over_week_change="rotation",
            investment_insight="stay long majors",
        ),
    )


def test_write_report_and_update_index(tmp_settings):
    r1 = _make_report("2026-04-14")
    r2 = _make_report("2026-04-15")
    storage_module.write_report(r1)
    storage_module.write_report(r2)

    index_path = tmp_settings.reports_dir / "index.json"
    assert index_path.exists()
    import json
    index = json.loads(index_path.read_text())
    # most recent first
    assert index[0]["date"] == "2026-04-15"
    assert index[1]["date"] == "2026-04-14"
    assert index[0]["top5"][0]["symbol"] == "ETH"


def test_update_index_deduplicates_same_date(tmp_settings):
    r1 = _make_report("2026-04-15")
    storage_module.write_report(r1)
    r1b = _make_report("2026-04-15")  # overwrite same day
    storage_module.write_report(r1b)
    import json
    index = json.loads((tmp_settings.reports_dir / "index.json").read_text())
    dates = [e["date"] for e in index]
    assert dates.count("2026-04-15") == 1
