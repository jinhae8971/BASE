"""Position sizing and the guards that stand between a signal and an order.

Every entry has to clear, in order:

1. **Regime gate** — ``risk_off`` multiplies exposure by 0, so no new risk.
2. **Daily loss kill** — once today's realised loss exceeds
   ``risk.daily_loss_kill_pct`` of equity, entries stop until tomorrow.
3. **Exposure cap** — total position value may not exceed
   ``risk.max_total_exposure_pct``, and ``risk.min_cash_buffer_pct`` of equity
   always stays in KRW.
4. **Per-trade sizing** — ``position_pct`` of tradable equity, scaled by the
   regime multiplier and by how far the score clears the threshold, then
   clipped to the min/max order size.

Long-term holdings never enter any of these numbers: the equity base is
*tradable* equity only, computed after :class:`~upbit.holdings.HoldingsGuard`
has carved the protected bag out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from common.logging import get_logger

from .broker import MIN_ORDER_KRW
from .store import UpbitStore, get_store, utc_now
from .strategy import UpbitConfig
from .types import MarketRegimeView, Regime

log = get_logger(__name__)


@dataclass
class EquityView:
    """Snapshot of what the engine is actually allowed to work with."""

    cash_krw: float = 0.0
    trading_value_krw: float = 0.0     # engine-held positions at market
    longterm_value_krw: float = 0.0    # protected bag, excluded from sizing
    unrealized_pnl: float = 0.0
    positions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def tradable_equity(self) -> float:
        """Cash plus engine positions — the base for every sizing decision."""
        return self.cash_krw + self.trading_value_krw

    @property
    def total_equity(self) -> float:
        return self.tradable_equity + self.longterm_value_krw

    @property
    def exposure_pct(self) -> float:
        base = self.tradable_equity
        return (self.trading_value_krw / base) if base > 0 else 0.0


@dataclass
class SizingDecision:
    approved: bool
    krw_amount: float = 0.0
    reason: str = ""


class RiskGuard:
    def __init__(self, config: UpbitConfig, store: UpbitStore | None = None) -> None:
        self.config = config
        self.store = store or get_store()

    # ------------------------------------------------------------------
    def realized_pnl_today(self) -> float:
        """Sum of realised P&L on positions closed since 00:00 UTC-relative day start."""
        since = (utc_now() - timedelta(hours=24)).isoformat(timespec="seconds")
        trades = self.store.list_trades(
            limit=500, side="ask", since=since, mode=self.config.mode
        )
        return sum(float(t.get("pnl") or 0.0) for t in trades)

    def daily_loss_breached(self, equity: EquityView) -> tuple[bool, float]:
        """``(halt?, loss_pct)`` — loss_pct is positive when we are down."""
        base = equity.tradable_equity
        if base <= 0:
            return False, 0.0
        pnl = self.realized_pnl_today()
        loss_pct = -pnl / base if pnl < 0 else 0.0
        return loss_pct >= self.config.risk.daily_loss_kill_pct, loss_pct

    # ------------------------------------------------------------------
    def regime_multiplier(self, regime: MarketRegimeView) -> float:
        configured = self.config.risk.regime_exposure.get(regime.regime.value)
        if configured is None:
            return regime.exposure_multiplier
        return float(configured)

    def available_capital(self, equity: EquityView, regime: MarketRegimeView) -> float:
        """KRW that may be deployed right now, after caps and the cash buffer."""
        base = equity.tradable_equity
        if base <= 0:
            return 0.0
        multiplier = self.regime_multiplier(regime)
        exposure_cap = base * self.config.risk.max_total_exposure_pct * multiplier
        room_by_exposure = max(exposure_cap - equity.trading_value_krw, 0.0)
        room_by_cash = max(equity.cash_krw - base * self.config.risk.min_cash_buffer_pct, 0.0)
        return min(room_by_exposure, room_by_cash)

    # ------------------------------------------------------------------
    def size_position(
        self,
        *,
        equity: EquityView,
        regime: MarketRegimeView,
        score: float,
        open_positions: int,
        remaining_capital: float,
    ) -> SizingDecision:
        cfg = self.config.strategy

        if regime.regime is Regime.RISK_OFF or self.regime_multiplier(regime) <= 0:
            return SizingDecision(False, reason="시장 국면 risk_off — 신규 진입 차단")
        if open_positions >= cfg.max_positions:
            return SizingDecision(False, reason=f"최대 보유 종목 수 {cfg.max_positions}개 도달")
        if score < cfg.min_score:
            return SizingDecision(False, reason=f"종합점수 {score:.1f} < 기준 {cfg.min_score:.1f}")

        base = equity.tradable_equity
        amount = base * cfg.position_pct * self.regime_multiplier(regime)

        # Conviction tilt: at the threshold you get 85% of a unit, at 100 you get 115%.
        span = max(100.0 - cfg.min_score, 1.0)
        amount *= 0.85 + 0.30 * min((score - cfg.min_score) / span, 1.0)

        if cfg.max_krw_per_trade > 0:
            amount = min(amount, cfg.max_krw_per_trade)
        amount = min(amount, remaining_capital)

        floor = max(cfg.min_krw_per_trade, MIN_ORDER_KRW)
        if amount < floor:
            return SizingDecision(
                False, reason=f"가용 자금 부족 (계산액 {amount:,.0f} < 최소 {floor:,.0f} KRW)"
            )
        return SizingDecision(True, krw_amount=float(int(amount)), reason="사이징 통과")

    # ------------------------------------------------------------------
    def exit_levels(self, entry_price: float) -> tuple[float, float]:
        """``(stop_price, take_price)`` for a fresh entry."""
        cfg = self.config.strategy
        return (
            entry_price * (1 - cfg.stop_loss_pct),
            entry_price * (1 + cfg.take_profit_pct),
        )

    def trailing_stop(self, entry_price: float, high_water: float) -> float | None:
        """Trailing level once the position has run far enough, else ``None``."""
        cfg = self.config.strategy
        if cfg.trailing_gap_pct <= 0:
            return None
        if high_water < entry_price * (1 + cfg.trailing_activate_pct):
            return None
        return high_water * (1 - cfg.trailing_gap_pct)
