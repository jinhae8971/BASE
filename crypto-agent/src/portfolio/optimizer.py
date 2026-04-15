"""Portfolio optimizer -- turns aggregated signals into target weights.

Phase 0: a simple long-only, top-K, equal-weight allocator bounded by risk
constraints from `Settings`. Phase 2 will upgrade to Risk Parity with a
1/2-Kelly overlay and a cash floor driven by the macro regime.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.config import get_settings
from src.portfolio.aggregator import AggregatedSignal


@dataclass
class Allocation:
    weights: dict[str, float]  # symbol -> percent (0..100)
    cash_pct: float


def optimize(
    signals: dict[str, AggregatedSignal],
    macro_cash_floor_pct: float = 20.0,
    top_k: int = 8,
) -> Allocation:
    s = get_settings()

    # Keep only positive scores, rank descending.
    positive = sorted(
        (sig for sig in signals.values() if sig.score > 0),
        key=lambda x: x.score,
        reverse=True,
    )[:top_k]

    if not positive:
        return Allocation(weights={sym: 0.0 for sym in signals}, cash_pct=100.0)

    investable_pct = max(0.0, 100.0 - macro_cash_floor_pct)
    total_score = sum(x.score for x in positive)
    weights = {sym: 0.0 for sym in signals}
    for sig in positive:
        raw = investable_pct * (sig.score / total_score)
        weights[sig.symbol] = min(raw, s.max_position_pct)

    # Enforce BTC+ETH core minimum.
    core = sum(weights.get(c, 0.0) for c in s.core_assets)
    if core < s.min_core_pct:
        deficit = s.min_core_pct - core
        # Top up BTC first, then ETH.
        for core_sym in s.core_assets:
            take = min(deficit, s.max_position_pct - weights.get(core_sym, 0.0))
            weights[core_sym] = weights.get(core_sym, 0.0) + take
            deficit -= take
            if deficit <= 0:
                break
        # Scale down non-core to rebalance to investable_pct.
        non_core_sum = sum(
            w for sym, w in weights.items() if sym not in s.core_assets
        )
        target_non_core = max(0.0, investable_pct - sum(weights[c] for c in s.core_assets))
        if non_core_sum > 0 and non_core_sum > target_non_core:
            scale = target_non_core / non_core_sum
            for sym in list(weights):
                if sym not in s.core_assets:
                    weights[sym] *= scale

    allocated = sum(weights.values())
    return Allocation(weights=weights, cash_pct=max(0.0, 100.0 - allocated))
