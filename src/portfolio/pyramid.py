"""Pyramiding — adding to winners that have already proven the thesis.

Philosophy: cut losers fast, **scale into winners**.

A position that runs +20% from entry has earned the right to a bigger
weight; +40% earns another step. We never pyramid below entry.

    Level 1: pnl >= +20% from entry  -> add cfg.step_pct of NAV
    Level 2: pnl >= +40% from entry  -> add another cfg.step_pct of NAV

Each level fires only once (tracked via ``position_state.pyramid_levels``).
The added weight is bounded by ``risk.max_position_weight`` and only fires
for tickers already in the day's target (we don't pyramid into names the
research engine no longer believes in).

This module mutates the ``PortfolioTarget.positions`` dict in-place; the
normal rebalance flow will pick up the new (higher) weight and fire BUY
orders through the standard execution path. The pyramid_levels counter is
only incremented after the BUY actually fills (handled in execution_agent).
"""
from __future__ import annotations

from dataclasses import dataclass

from common.config import get_setting
from common.logging import get_logger
from common.types import PortfolioTarget

from .position_state import PositionState, all_states

log = get_logger(__name__)


@dataclass
class PyramidIntent:
    ticker: str
    new_target_weight: float
    levels_added: int
    pnl_pct: float
    reason: str


def evaluate_pyramid(
    target: PortfolioTarget,
    current_positions: dict[str, int],
    prices: dict[str, float],
    nav: float,
) -> list[PyramidIntent]:
    """Decide which winners get pyramided. Mutates target.positions in-place.

    Returns the list of intents so the caller can journal/notify.
    """
    if nav <= 0:
        return []
    triggers: list[float] = list(get_setting("execution.pyramid_triggers", [0.20, 0.40]))
    step_pct: float = float(get_setting("execution.pyramid_step_pct", 0.025))
    max_pos: float = float(get_setting("risk.max_position_weight", 0.10))

    intents: list[PyramidIntent] = []
    by_ticker: dict[str, PositionState] = {s.ticker: s for s in all_states()}

    for ticker in list(target.positions.keys()):
        state = by_ticker.get(ticker)
        if state is None or state.entry_price <= 0:
            continue
        price = prices.get(ticker)
        if not price:
            continue
        pnl = state.total_pnl_pct(price)
        levels_due = sum(1 for trigger in triggers if pnl >= trigger)
        new_levels = levels_due - state.pyramid_levels
        if new_levels <= 0:
            continue

        current_qty = current_positions.get(ticker, 0)
        current_w = (current_qty * price) / nav
        add_w = step_pct * new_levels
        new_w = min(current_w + add_w, max_pos)
        if new_w <= current_w + 1e-6:
            continue

        # Update the target so the rebalance flow naturally produces the BUY.
        # We pin to max(target_weight, new_w) so we never *shrink* a position
        # that the research engine wanted bigger anyway.
        prior_target = target.positions.get(ticker, 0.0)
        target.positions[ticker] = max(prior_target, new_w)
        intents.append(
            PyramidIntent(
                ticker=ticker,
                new_target_weight=target.positions[ticker],
                levels_added=new_levels,
                pnl_pct=pnl,
                reason=(
                    f"pyramid +{new_levels} ({pnl:+.2%} from entry, "
                    f"target {prior_target:.1%}->{target.positions[ticker]:.1%})"
                ),
            )
        )
        log.info(
            "pyramid.add",
            ticker=ticker,
            pnl_pct=round(pnl, 4),
            levels=new_levels,
            new_weight=round(target.positions[ticker], 4),
        )
    return intents


def commit_pyramid_levels(intents: list[PyramidIntent]) -> None:
    """Bump the persisted level counter so we don't fire the same level twice.

    Called after the BUY orders are submitted (we count an in-flight order as
    'committed' — partial fills get reconciled at EOD).
    """
    from .position_state import get as get_state
    from .position_state import set_pyramid_level

    for intent in intents:
        state = get_state(intent.ticker)
        if state is None:
            continue
        set_pyramid_level(intent.ticker, state.pyramid_levels + intent.levels_added)
