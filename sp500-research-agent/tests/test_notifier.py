"""Tests for Telegram message formatting."""
from __future__ import annotations

from datetime import UTC, datetime

from src.models import (
    DailyReport,
    GainerStock,
    NarrativeInsight,
    StockAnalysis,
)
from src.notifier import _h, _format_message


def test_h_escapes_html_chars():
    assert _h("a < b & c") == "a &lt; b &amp; c"
    assert _h("S&P 500") == "S&amp;P 500"
    assert _h("use <script>") == "use &lt;script&gt;"


def test_format_message_includes_dashboard_link():
    report = DailyReport(
        date="2026-04-16",
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
                pump_thesis="Blackwell GPU demand exceeds supply",
                drivers=["AI capex"],
                risks=["valuation"],
                sector_tags=["Technology"],
                confidence=0.85,
            )
        ],
        narrative=NarrativeInsight(
            current_narrative="AI infrastructure spending boom",
            hot_sectors=["Technology"],
            cooling_sectors=["Utilities"],
            week_over_week_change="broadening",
            investment_insight="overweight AI infra",
        ),
    )
    msg = _format_message(report, "https://example.github.io/sp500/")
    assert "2026-04-16" in msg
    assert "<b>" in msg
    assert "NVDA" in msg
    assert "+12.3%" in msg
    assert "report.html?date=2026-04-16" in msg
