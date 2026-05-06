"""Per-name stop-loss / trailing take-profit logic — asymmetric (long-only).

Philosophy: cut losers fast, let winners run.

    Hard stop-loss:        current_price / entry_price - 1 <= -hard_stop_pct
    Trailing take-profit:  current_price / peak_price - 1  <= -trailing_pct

The trailing rule fires only when the position is *already in profit* — we
don't whipsaw out of a fresh BUY that hasn't moved up yet.

Defaults (overridable in settings.yaml::risk):
    hard_stop_pct          0.12     (12% from entry)
    trailing_take_pct      0.10     (10% from peak; only after +5% from entry)
    trailing_min_profit    0.05
"""
from __future__ import annotations

from dataclasses import dataclass

from common.config import get_setting
from common.logging import get_logger
from common.types import Order, Side

from .position_state import all_states

log = get_logger(__name__)


@dataclass
class StopSignal:
    ticker: str
    qty: int
    reason: str
    pnl_pct: float


def evaluate_stops(
    current_positions: dict[str, int],
    prices: dict[str, float],
) -> list[StopSignal]:
    """Walk the persisted position_state and emit forced-SELL signals."""
    hard_stop = float(get_setting("risk.hard_stop_pct", 0.12))
    trailing = float(get_setting("risk.trailing_take_pct", 0.10))
    min_profit = float(get_setting("risk.trailing_min_profit", 0.05))

    signals: list[StopSignal] = []
    by_ticker = {s.ticker: s for s in all_states()}

    for ticker, qty in current_positions.items():
        if qty <= 0:
            continue
        state = by_ticker.get(ticker)
        if state is None or state.entry_price <= 0:
            continue
        price = prices.get(ticker)
        if not price:
            continue
        total_pnl = state.total_pnl_pct(price)
        trail_drop = state.trailing_drop_pct(price)

        if total_pnl <= -hard_stop:
            signals.append(
                StopSignal(
                    ticker=ticker,
                    qty=qty,
                    reason=f"hard_stop {total_pnl:.2%} <= -{hard_stop:.0%}",
                    pnl_pct=total_pnl,
                )
            )
            continue
        if total_pnl >= min_profit and trail_drop <= -trailing:
            signals.append(
                StopSignal(
                    ticker=ticker,
                    qty=qty,
                    reason=f"trail {trail_drop:.2%} from peak (pnl {total_pnl:+.2%})",
                    pnl_pct=total_pnl,
                )
            )
    return signals


def stop_orders(signals: list[StopSignal], prices: dict[str, float]) -> list[Order]:
    """Convert stop signals into market-style SELL orders.

    We use ``order_type='market'`` — when a stop triggers, getting filled is
    more important than getting a tight price.
    """
    orders: list[Order] = []
    for s in signals:
        price = prices.get(s.ticker, 0.0)
        if price <= 0 or s.qty <= 0:
            continue
        log.warning("stops.fired", ticker=s.ticker, reason=s.reason)
        orders.append(
            Order(
                ticker=s.ticker,
                side=Side.SELL,
                quantity=s.qty,
                price=price,
                order_type="market",
            )
        )
    return orders


def update_peaks_from_prices(prices: dict[str, float], today: object) -> None:
    """Walk through every persisted position and bump peak if today exceeded it.

    Call once at end-of-day with today's closes.
    """
    from datetime import date

    from .position_state import update_peak

    today_d = today if isinstance(today, date) else date.today()
    for state in all_states():
        px = prices.get(state.ticker)
        if px:
            update_peak(state.ticker, px, today_d)


def apply_state_after_fill(
    ticker: str, side: Side, fill_qty: int, fill_price: float, today: object
) -> None:
    """Maintain position_state after a real (or simulated) fill."""
    from datetime import date

    from .position_state import remove, upsert_on_buy

    today_d = today if isinstance(today, date) else date.today()
    if side is Side.BUY:
        upsert_on_buy(ticker, fill_price, fill_qty, today_d)
    elif side is Side.SELL:
        # If the position is fully closed, drop the row so trailing peak resets
        # next time we BUY back in.
        remove(ticker)
