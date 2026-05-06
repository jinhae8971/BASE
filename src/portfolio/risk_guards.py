"""Daily portfolio-level risk circuit breakers.

These are the guards documented in ``docs/risk_policy.md`` but previously not
enforced in code. They sit *between* the optimizer and the broker, and have
authority to cancel or scale down orders before they reach KIS.

Rules
-----
``daily_loss_kill`` — if today's NAV is down more than X % vs. yesterday's
                      close, halt all new orders for the day.

``max_portfolio_mdd`` — if rolling 252-day MDD breaches X %, force the
                        target to a defensive cash weight (de-leverage).

``max_turnover_daily`` — if today's already-executed turnover (cumulative
                         order notional / NAV) exceeds X %, cancel further
                         orders.

Each guard returns a ``GuardDecision`` instead of raising so the caller can
log + journal the reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from common.config import get_setting
from common.logging import get_logger
from common.types import Order, PortfolioTarget

log = get_logger(__name__)


@dataclass
class GuardDecision:
    allow: bool
    reason: str = ""
    scale: float = 1.0
    target_override: PortfolioTarget | None = None
    triggered: list[str] = field(default_factory=list)


class DailyRiskGuard:
    def __init__(
        self,
        *,
        prev_nav: float | None = None,
        equity_curve: pd.Series | None = None,
        executed_today_notional: float = 0.0,
    ) -> None:
        self.prev_nav = prev_nav
        self.equity_curve = equity_curve if equity_curve is not None else pd.Series(dtype=float)
        self.executed_today_notional = float(executed_today_notional)

        self.daily_loss_kill = float(get_setting("risk.daily_loss_kill", 0.03))
        self.max_mdd = float(get_setting("risk.max_portfolio_mdd", 0.15))
        self.max_turnover = float(get_setting("risk.max_turnover_daily", 0.30))
        self.cash_buffer_min = float(get_setting("risk.cash_buffer_min", 0.05))

    # ------------------------------------------------------------------
    def evaluate(
        self,
        target: PortfolioTarget,
        nav_today: float,
        new_orders_notional: float,
    ) -> GuardDecision:
        """Decide whether to allow ``new_orders_notional`` of trades on top
        of ``executed_today_notional`` given the day's NAV path."""
        triggered: list[str] = []

        # ── Daily loss kill ─────────────────────────────────────────────
        if self.prev_nav and self.prev_nav > 0:
            today_return = nav_today / self.prev_nav - 1
            if today_return <= -self.daily_loss_kill:
                triggered.append(f"daily_loss_kill ({today_return:.2%})")
                log.warning("guard.daily_loss_kill", return_pct=today_return)
                return GuardDecision(
                    allow=False,
                    reason=f"Daily loss {today_return:.2%} ≤ -{self.daily_loss_kill:.0%}",
                    triggered=triggered,
                )

        # ── Portfolio MDD trigger → graduated de-leverage ───────────────
        # Tiers (from settings):
        #   risk.graduated_mdd: [0.10, 0.12, 0.15]  → trim 25%, 50%, 75%
        if not self.equity_curve.empty:
            mdd = self._rolling_mdd(self.equity_curve)
            tiers = get_setting("risk.graduated_mdd", [0.10, 0.12, 0.15]) or []
            trims = [0.25, 0.50, 0.75]
            trim_pct = 0.0
            for level, t in zip(tiers, trims, strict=False):
                if mdd <= -float(level):
                    trim_pct = t
            if trim_pct > 0:
                keep = 1.0 - trim_pct
                triggered.append(f"mdd_trigger ({mdd:.2%} → trim {trim_pct:.0%})")
                trimmed = {t: w * keep for t, w in target.positions.items()}
                cash = max(1.0 - sum(trimmed.values()), self.cash_buffer_min * 2)
                override = PortfolioTarget(
                    as_of=target.as_of,
                    cash_weight=cash,
                    positions=trimmed,
                    rationale=(
                        f"{target.rationale} | MDD guard: rolling MDD={mdd:.2%}; "
                        f"trimming {trim_pct:.0%}, cash>={cash:.0%}"
                    ),
                )
                log.warning("guard.mdd_trigger", mdd=mdd, trim=trim_pct)
                return GuardDecision(
                    allow=True,
                    reason=f"MDD {mdd:.2%} -> trim {trim_pct:.0%}",
                    scale=keep,
                    target_override=override,
                    triggered=triggered,
                )

        # ── Turnover cap ────────────────────────────────────────────────
        if nav_today > 0:
            cumulative = (self.executed_today_notional + new_orders_notional) / nav_today
            if cumulative > self.max_turnover:
                triggered.append(f"turnover_cap ({cumulative:.2%})")
                # Allowed = max_turnover - already executed
                remaining = max(self.max_turnover * nav_today - self.executed_today_notional, 0)
                scale = (
                    0.0
                    if new_orders_notional <= 0
                    else remaining / new_orders_notional
                )
                log.warning("guard.turnover_cap", cumulative=cumulative, scale=scale)
                return GuardDecision(
                    allow=scale > 0,
                    reason=f"Daily turnover would hit {cumulative:.2%}",
                    scale=max(0.0, min(1.0, scale)),
                    triggered=triggered,
                )

        return GuardDecision(allow=True)

    # ------------------------------------------------------------------
    def apply_to_orders(
        self, orders: list[Order], decision: GuardDecision
    ) -> list[Order]:
        if not decision.allow:
            log.warning("guard.cancel_all_orders", reason=decision.reason)
            return []
        if decision.scale < 1.0:
            adjusted: list[Order] = []
            for o in orders:
                qty = max(int(o.quantity * decision.scale), 0)
                if qty > 0:
                    adjusted.append(o.model_copy(update={"quantity": qty}))
            return adjusted
        return orders

    # ------------------------------------------------------------------
    def _rolling_mdd(self, eq: pd.Series, window: int = 252) -> float:
        if eq.empty:
            return 0.0
        tail = eq.tail(window)
        peak = tail.cummax()
        dd = (tail - peak) / peak
        return float(dd.min())


def load_recent_equity(
    data_dir: str | None = None, *, lookback_days: int = 252
) -> pd.Series:
    """Load the persisted NAV curve so guards can evaluate rolling MDD.

    The curve is appended by ``persist_nav`` at end-of-day. Returns an empty
    series if the file is missing.
    """
    from pathlib import Path

    from common.config import get_env

    p = Path(data_dir or get_env().mais_data_dir) / "nav_history.csv"
    if not p.exists():
        return pd.Series(dtype=float)
    try:
        df = pd.read_csv(p, parse_dates=["date"], index_col="date")
        return df["nav"].astype(float).tail(lookback_days)
    except Exception as e:
        log.debug("nav_history.load_failed", error=str(e))
        return pd.Series(dtype=float)


def persist_nav(when: date, nav: float, data_dir: str | None = None) -> None:
    """Append today's NAV snapshot for tomorrow's guard evaluation."""
    from pathlib import Path

    from common.config import get_env

    p = Path(data_dir or get_env().mais_data_dir) / "nav_history.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    header = not p.exists()
    with p.open("a", encoding="utf-8") as f:
        if header:
            f.write("date,nav\n")
        f.write(f"{when.isoformat()},{nav:.2f}\n")


def daily_executed_notional(
    journal_rows: list[dict[str, Any]], today: date
) -> float:
    """Sum |qty x price| of execution-agent rows journaled today."""
    total = 0.0
    for r in journal_rows:
        if r.get("agent") != "execution":
            continue
        ts = str(r.get("ts") or "")[:10]
        if ts != today.isoformat():
            continue
        ctx = r.get("context_json")
        try:
            import json as _json

            payload = _json.loads(ctx) if isinstance(ctx, str) else (ctx or {})
        except Exception:
            continue
        order = (payload or {}).get("order") or {}
        qty = float(order.get("quantity", 0))
        price = float(order.get("price") or 0)
        total += abs(qty * price)
    return total
