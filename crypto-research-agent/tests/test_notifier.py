"""Tests for Telegram message formatting."""
from __future__ import annotations

from datetime import UTC, datetime

from src.models import (
    CoinAnalysis,
    DailyReport,
    GainerCoin,
    NarrativeInsight,
)
from src.notifier import _escape_md, _format_message


def test_escape_md_handles_special_chars():
    assert _escape_md("Hello (world)!") == "Hello \\(world\\)\\!"
    assert _escape_md("a.b") == "a\\.b"


def test_format_message_includes_dashboard_link():
    report = DailyReport(
        date="2026-04-16",
        generated_at=datetime.now(UTC),
        gainers=[
            GainerCoin(
                id="tao",
                symbol="tao",
                name="Bittensor",
                current_price=500.0,
                market_cap=4e9,
                market_cap_rank=30,
                total_volume=2e8,
                change_48h_pct=42.3,
            )
        ],
        analyses=[
            CoinAnalysis(
                coin_id="tao",
                symbol="TAO",
                pump_thesis="AI rotation + subnet demand",
                drivers=["subnet demand"],
                risks=["unlock"],
                category_tags=["AI"],
                confidence=0.7,
            )
        ],
        narrative=NarrativeInsight(
            current_narrative="AI rotation accelerating",
            hot_sectors=["AI"],
            cooling_sectors=["Meme"],
            week_over_week_change="shift from meme",
            investment_insight="overweight AI basket",
        ),
    )
    msg = _format_message(report, "https://example.github.io/crypto/")
    assert "2026\\-04\\-16" in msg
    assert "TAO" in msg
    assert "\\+42\\.3" in msg
    assert "report.html?date=2026-04-16" in msg.replace("\\", "")
