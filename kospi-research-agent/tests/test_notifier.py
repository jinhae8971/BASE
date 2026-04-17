"""Tests for Telegram message formatting."""
from __future__ import annotations

from datetime import UTC, datetime

from src.models import (
    DailyReport,
    GainerStock,
    NarrativeInsight,
    StockAnalysis,
)
from src.notifier import _esc, _format_message


def test_esc_handles_special_chars():
    assert _esc("Hello (world)!") == "Hello \\(world\\)\\!"
    assert _esc("a.b") == "a\\.b"


def test_format_message_includes_dashboard_link():
    report = DailyReport(
        date="2026-04-16",
        generated_at=datetime.now(UTC),
        market_type="KOSPI",
        gainers=[
            GainerStock(
                ticker="005930",
                name="삼성전자",
                market="KOSPI",
                close=72000,
                market_cap=4.3e14,
                trading_value=1.5e12,
                volume=20_000_000,
                change_pct_1d=3.5,
                change_pct_nd=5.8,
            )
        ],
        analyses=[
            StockAnalysis(
                ticker="005930",
                name="삼성전자",
                pump_thesis="HBM 수주 기대감 + 외국인 순매수",
                drivers=["HBM"],
                risks=["환율"],
                sector_tags=["반도체"],
                confidence=0.8,
            )
        ],
        narrative=NarrativeInsight(
            current_narrative="반도체 수요 회복",
            hot_sectors=["반도체"],
            cooling_sectors=["바이오"],
            week_over_week_change="shift",
            investment_insight="반도체 비중 확대 유지",
        ),
    )
    msg = _format_message(report, "https://example.github.io/kospi/")
    assert "2026\\-04\\-16" in msg
    assert "삼성전자" in msg
    assert "005930" in msg
    assert "\\+5\\.8" in msg
    assert "report.html?date=2026-04-16" in msg.replace("\\", "")
