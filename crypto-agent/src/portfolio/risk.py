"""Risk guardrails.

These checks run *after* the executor agent has proposed orders and *before*
anything hits Binance. A violation downgrades, skips, or halts the order —
never silently mutates sizing without logging the change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.config import get_settings
from src.logging import get_logger

log = get_logger("risk")


class GuardAction(str, Enum):
    ALLOW = "allow"
    DOWNSIZE = "downsize"
    SKIP = "skip"
    HALT_ALL = "halt_all"


@dataclass
class GuardDecision:
    action: GuardAction
    reason: str
    new_qty_usd: float | None = None


@dataclass
class PortfolioState:
    equity_usd: float
    peak_equity_usd: float
    daily_pnl_pct: float
    weekly_pnl_pct: float


def check_mdd(state: PortfolioState) -> GuardDecision:
    s = get_settings()
    if state.peak_equity_usd <= 0:
        return GuardDecision(GuardAction.ALLOW, "no peak yet")
    dd_pct = 100.0 * (1 - state.equity_usd / state.peak_equity_usd)
    if dd_pct >= s.mdd_circuit_breaker_pct:
        return GuardDecision(
            GuardAction.HALT_ALL,
            f"MDD {dd_pct:.2f}% >= {s.mdd_circuit_breaker_pct:.2f}% — circuit breaker",
        )
    return GuardDecision(GuardAction.ALLOW, f"MDD {dd_pct:.2f}% within budget")


def check_daily(state: PortfolioState) -> GuardDecision:
    s = get_settings()
    if state.daily_pnl_pct <= -s.daily_loss_halt_pct:
        return GuardDecision(
            GuardAction.SKIP,
            f"daily P&L {state.daily_pnl_pct:.2f}% below -{s.daily_loss_halt_pct}%",
        )
    return GuardDecision(GuardAction.ALLOW, "")


def check_order_sanity(order_qty_usd: float, equity_usd: float) -> GuardDecision:
    if equity_usd <= 0:
        return GuardDecision(GuardAction.SKIP, "zero equity")
    pct = 100.0 * order_qty_usd / equity_usd
    if pct > 30.0:
        return GuardDecision(
            GuardAction.DOWNSIZE,
            f"order {pct:.1f}% of equity > 30%",
            new_qty_usd=equity_usd * 0.30,
        )
    return GuardDecision(GuardAction.ALLOW, "")
