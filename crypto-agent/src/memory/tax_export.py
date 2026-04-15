"""Korean virtual-asset tax export.

Starting with the 2025 tax year Korea treats realized gains on virtual
assets as `other-income` (other income), taxed at 22% (20% income tax + 2%
local tax) on net gain above a KRW 2.5M annual deduction. This module
produces two things operators can hand to an accountant:

  1. A line-by-line realized-gain ledger in both JSON and CSV.
  2. An annual aggregate totaling gains, losses, net gain, and the
     estimated tax liability under the default rate.

Cost-basis method: **weighted average** (weighted-average) per symbol, which is
the method Korean NTS has indicated they will default to. We do not
attempt to support other methods (FIFO, specific ID) yet -- add them if
and when the guidance settles.

KRW conversion: the export accepts a `usd_to_krw` rate as a float. For a
production run the operator should pull the daily MOEF rate and pass
it in per-trade; this module doesn't fetch FX itself to keep the import
surface small.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from src.learning.position_tracker import ClosedPosition

# Default tax parameters (update with the final NTS schedule once it lands).
DEFAULT_TAX_RATE = 0.22           # 20% other-income-tax + 2% local-income-tax
DEFAULT_ANNUAL_DEDUCTION_KRW = 2_500_000


@dataclass
class RealizedTrade:
    symbol: str
    opened_at: str
    closed_at: str
    holding_period_days: float
    avg_entry_price_usd: float
    exit_price_usd: float
    qty: float
    proceeds_usd: float
    cost_basis_usd: float
    realized_pnl_usd: float
    realized_pnl_krw: float
    fx_rate_usd_krw: float


@dataclass
class AnnualReport:
    year: int
    gross_gain_krw: float
    gross_loss_krw: float
    net_gain_krw: float
    annual_deduction_krw: float
    taxable_gain_krw: float
    tax_rate: float
    estimated_tax_krw: float
    trade_count: int
    trades: list[RealizedTrade] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trades"] = [asdict(t) for t in self.trades]
        return d


# ---------------------------------------------------------------------------
# Conversion from ClosedPosition -> RealizedTrade
# ---------------------------------------------------------------------------


def closed_to_realized(
    closed: ClosedPosition,
    usd_to_krw: float,
) -> RealizedTrade:
    proceeds = closed.exit_price * closed.total_qty
    cost = closed.avg_entry_price * closed.total_qty
    pnl_usd = closed.realized_pnl_usd
    pnl_krw = pnl_usd * usd_to_krw
    return RealizedTrade(
        symbol=closed.symbol,
        opened_at=closed.opened_at.isoformat(),
        closed_at=closed.closed_at.isoformat(),
        holding_period_days=round(closed.holding_period_days, 4),
        avg_entry_price_usd=closed.avg_entry_price,
        exit_price_usd=closed.exit_price,
        qty=closed.total_qty,
        proceeds_usd=proceeds,
        cost_basis_usd=cost,
        realized_pnl_usd=pnl_usd,
        realized_pnl_krw=pnl_krw,
        fx_rate_usd_krw=usd_to_krw,
    )


# ---------------------------------------------------------------------------
# Annual aggregation
# ---------------------------------------------------------------------------


def build_annual_report(
    year: int,
    closed_positions: Iterable[ClosedPosition],
    usd_to_krw: float,
    *,
    tax_rate: float = DEFAULT_TAX_RATE,
    annual_deduction_krw: float = DEFAULT_ANNUAL_DEDUCTION_KRW,
) -> AnnualReport:
    """Aggregate all closes whose `closed_at.year == year` into a tax report.

    Positions closed in other years are silently skipped so callers can
    pass their entire history without pre-filtering.
    """
    trades: list[RealizedTrade] = []
    gross_gain = 0.0
    gross_loss = 0.0

    for closed in closed_positions:
        if closed.closed_at.year != year:
            continue
        rt = closed_to_realized(closed, usd_to_krw)
        trades.append(rt)
        if rt.realized_pnl_krw >= 0:
            gross_gain += rt.realized_pnl_krw
        else:
            gross_loss += rt.realized_pnl_krw  # negative; adds to total

    net = gross_gain + gross_loss  # gross_loss is already negative
    taxable = max(0.0, net - annual_deduction_krw)
    tax = taxable * tax_rate

    return AnnualReport(
        year=year,
        gross_gain_krw=gross_gain,
        gross_loss_krw=gross_loss,
        net_gain_krw=net,
        annual_deduction_krw=annual_deduction_krw,
        taxable_gain_krw=taxable,
        tax_rate=tax_rate,
        estimated_tax_krw=tax,
        trade_count=len(trades),
        trades=trades,
    )


# ---------------------------------------------------------------------------
# Output: JSON + CSV
# ---------------------------------------------------------------------------


def to_json(report: AnnualReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


def to_csv(report: AnnualReport) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "symbol",
            "opened_at",
            "closed_at",
            "holding_period_days",
            "qty",
            "avg_entry_price_usd",
            "exit_price_usd",
            "proceeds_usd",
            "cost_basis_usd",
            "realized_pnl_usd",
            "fx_rate_usd_krw",
            "realized_pnl_krw",
        ]
    )
    for t in report.trades:
        writer.writerow(
            [
                t.symbol,
                t.opened_at,
                t.closed_at,
                t.holding_period_days,
                t.qty,
                t.avg_entry_price_usd,
                t.exit_price_usd,
                t.proceeds_usd,
                t.cost_basis_usd,
                t.realized_pnl_usd,
                t.fx_rate_usd_krw,
                t.realized_pnl_krw,
            ]
        )
    return buf.getvalue()


def write_report(
    report: AnnualReport,
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"kr_tax_{report.year}.json"
    csv_path = output_dir / f"kr_tax_{report.year}_trades.csv"
    json_path.write_text(to_json(report), encoding="utf-8")
    csv_path.write_text(to_csv(report), encoding="utf-8")
    return json_path, csv_path
