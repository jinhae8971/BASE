"""Korean tax export tests -- realized P&L aggregation + KRW conversion."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.learning.position_tracker import ClosedPosition, EntryContext
from src.memory.tax_export import (
    DEFAULT_ANNUAL_DEDUCTION_KRW,
    DEFAULT_TAX_RATE,
    build_annual_report,
    closed_to_realized,
    to_csv,
    to_json,
    write_report,
)


def _closed(
    symbol: str = "BTCUSDT",
    *,
    entry: float = 100.0,
    exit: float = 150.0,
    qty: float = 2.0,
    opened: datetime = datetime(2025, 3, 1, tzinfo=UTC),
    closed: datetime = datetime(2025, 4, 1, tzinfo=UTC),
) -> ClosedPosition:
    pnl = (exit - entry) * qty
    return ClosedPosition(
        symbol=symbol,
        avg_entry_price=entry,
        exit_price=exit,
        total_qty=qty,
        opened_at=opened,
        closed_at=closed,
        realized_pnl_usd=pnl,
        entry=EntryContext(
            run_id="r1",
            as_of=opened,
            agent_payloads={},
            macro_regime="risk-on",
            universe=[symbol],
        ),
    )


# ---------------------------------------------------------------------------
# closed_to_realized
# ---------------------------------------------------------------------------


def test_closed_to_realized_computes_usd_and_krw_pnl() -> None:
    closed = _closed(entry=100.0, exit=150.0, qty=2.0)
    rt = closed_to_realized(closed, usd_to_krw=1_350.0)

    assert rt.cost_basis_usd == pytest.approx(200.0)
    assert rt.proceeds_usd == pytest.approx(300.0)
    assert rt.realized_pnl_usd == pytest.approx(100.0)
    assert rt.realized_pnl_krw == pytest.approx(135_000.0)
    assert rt.fx_rate_usd_krw == 1_350.0


# ---------------------------------------------------------------------------
# build_annual_report aggregation
# ---------------------------------------------------------------------------


def test_annual_report_filters_by_closed_at_year() -> None:
    positions = [
        _closed("BTCUSDT",
                opened=datetime(2024, 12, 1, tzinfo=UTC),
                closed=datetime(2024, 12, 10, tzinfo=UTC)),
        _closed("BTCUSDT",
                opened=datetime(2025, 1, 1, tzinfo=UTC),
                closed=datetime(2025, 2, 1, tzinfo=UTC)),
        _closed("ETHUSDT", entry=1000, exit=1200, qty=1.0,
                opened=datetime(2025, 3, 1, tzinfo=UTC),
                closed=datetime(2025, 4, 1, tzinfo=UTC)),
    ]
    report = build_annual_report(year=2025, closed_positions=positions, usd_to_krw=1_300)
    # Only two 2025 trades included.
    assert report.trade_count == 2
    assert {t.symbol for t in report.trades} == {"BTCUSDT", "ETHUSDT"}


def test_annual_report_applies_deduction_and_tax_rate() -> None:
    # Single winning trade of $10,000 at 1,300 KRW/USD = 13,000,000 KRW
    big_win = _closed(entry=100, exit=600, qty=20.0)
    report = build_annual_report(
        year=2025,
        closed_positions=[big_win],
        usd_to_krw=1_300,
    )
    assert report.gross_gain_krw == pytest.approx(13_000_000)
    assert report.gross_loss_krw == pytest.approx(0.0)
    assert report.net_gain_krw == pytest.approx(13_000_000)
    assert report.annual_deduction_krw == DEFAULT_ANNUAL_DEDUCTION_KRW
    expected_taxable = 13_000_000 - DEFAULT_ANNUAL_DEDUCTION_KRW
    assert report.taxable_gain_krw == pytest.approx(expected_taxable)
    assert report.tax_rate == DEFAULT_TAX_RATE
    assert report.estimated_tax_krw == pytest.approx(expected_taxable * DEFAULT_TAX_RATE)


def test_annual_report_net_gain_below_deduction_has_zero_tax() -> None:
    small_win = _closed(entry=100, exit=101, qty=1.0)  # $1 USD * FX
    report = build_annual_report(
        year=2025,
        closed_positions=[small_win],
        usd_to_krw=1_300,
    )
    assert report.net_gain_krw < DEFAULT_ANNUAL_DEDUCTION_KRW
    assert report.taxable_gain_krw == 0.0
    assert report.estimated_tax_krw == 0.0


def test_annual_report_mixes_gains_and_losses() -> None:
    win = _closed("BTCUSDT", entry=100, exit=200, qty=1.0)     # +100 USD
    loss = _closed("ETHUSDT", entry=1000, exit=900, qty=0.5)   # -50 USD
    report = build_annual_report(
        year=2025,
        closed_positions=[win, loss],
        usd_to_krw=1_000,
    )
    assert report.gross_gain_krw == pytest.approx(100_000)
    assert report.gross_loss_krw == pytest.approx(-50_000)
    assert report.net_gain_krw == pytest.approx(50_000)
    assert report.trade_count == 2


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------


def test_to_json_round_trips() -> None:
    import json

    report = build_annual_report(
        year=2025,
        closed_positions=[_closed()],
        usd_to_krw=1_300,
    )
    doc = json.loads(to_json(report))
    assert doc["year"] == 2025
    assert doc["trade_count"] == 1
    assert doc["trades"][0]["symbol"] == "BTCUSDT"


def test_to_csv_includes_header_and_one_row_per_trade() -> None:
    report = build_annual_report(
        year=2025,
        closed_positions=[_closed("BTCUSDT"), _closed("ETHUSDT")],
        usd_to_krw=1_300,
    )
    csv_text = to_csv(report)
    lines = csv_text.strip().splitlines()
    assert len(lines) == 3  # 1 header + 2 trades
    assert lines[0].startswith("symbol,")


def test_write_report_creates_both_files(tmp_path: Path) -> None:
    report = build_annual_report(
        year=2025,
        closed_positions=[_closed()],
        usd_to_krw=1_300,
    )
    json_path, csv_path = write_report(report, output_dir=tmp_path / "tax")
    assert json_path.exists()
    assert csv_path.exists()
    assert json_path.name == "kr_tax_2025.json"
    assert csv_path.name == "kr_tax_2025_trades.csv"
