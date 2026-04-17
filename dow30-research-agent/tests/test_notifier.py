"""Tests for Telegram message formatting."""
from __future__ import annotations

from datetime import UTC, datetime

from src.models import DailyReport, GainerStock, NarrativeInsight, StockAnalysis
from src.notifier import _h, _format_message


def test_h_escapes_html_chars():
    assert _h("a < b & c") == "a &lt; b &amp; c"
    assert _h("use <script>") == "use &lt;script&gt;"


def test_format_message():
    report = DailyReport(
        date="2026-04-16", generated_at=datetime.now(UTC),
        gainers=[GainerStock(ticker="UNH", name="UnitedHealth", sector="Healthcare",
                             close=530, market_cap=4.9e11, trading_value=3e9, volume=5e6,
                             change_pct_1d=3.0, change_pct_nd=6.5)],
        analyses=[StockAnalysis(ticker="UNH", name="UnitedHealth",
                                pump_thesis="Earnings beat + raised guidance",
                                drivers=["earnings"], risks=["regulation"],
                                sector_tags=["Healthcare"], confidence=0.8)],
        narrative=NarrativeInsight(current_narrative="Defensive rotation",
                                   hot_sectors=["Healthcare"], cooling_sectors=["Technology"],
                                   week_over_week_change="shift",
                                   investment_insight="lean defensive"),
    )
    msg = _format_message(report, "https://example.github.io/dow30/")
    assert "다우30" in msg
    assert "<b>" in msg
    assert "UNH" in msg
    assert "+6.5%" in msg
    assert "report.html?date=2026-04-16" in msg
