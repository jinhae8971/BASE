"""Defensive spot-only hedge — long-only, no futures, no shorts.

When the macro regime hint flips to risk_off (or rolling MDD breaches),
the optimizer's cash bucket is partly redirected to defensive spot ETFs:

    411060   ACE KRX금현물            (spot gold, no futures)
    304660   KOSEF 단기자금            (KRW money market)
    132030   KODEX 국고채30년액티브    (long-duration KRW bonds)
    302190   TIGER 미국S&P500선물(H)   ← excluded (futures-based)

Only the spot products above ship in the default basket so we honour the
"no futures" constraint. Operators can edit ``hedge.basket`` in
settings.yaml to add/remove products.

How it plugs in:
    1. After PortfolioOptimizer produces a target with cash_weight > 0,
       ``apply_defensive_hedge(target, regime)`` may shift up to
       ``hedge.max_hedge_weight`` of cash into the basket.
    2. Allocation per ETF is proportional to the basket weights.
    3. The ETFs go into ``target.positions`` like any other ticker, so
       all downstream guards (per-name cap, ADV, stops, pyramid) work
       transparently.
"""
from __future__ import annotations

from common.config import get_setting
from common.logging import get_logger
from common.types import MarketRegime, PortfolioTarget

log = get_logger(__name__)

# Spot-only Korean defensive ETFs (no futures, no leverage)
DEFAULT_BASKET: dict[str, dict[str, float | str]] = {
    "411060": {"name": "ACE KRX금현물", "weight": 0.50, "kind": "gold_spot"},
    "304660": {"name": "KOSEF 단기자금", "weight": 0.30, "kind": "money_market"},
    "132030": {"name": "KODEX 국고채30년", "weight": 0.20, "kind": "long_bond"},
}


def _basket() -> dict[str, dict[str, float | str]]:
    """Read the basket from settings, falling back to DEFAULT_BASKET."""
    cfg = get_setting("hedge.basket", None)
    if not cfg or not isinstance(cfg, dict):
        return DEFAULT_BASKET
    # Operator-supplied — keep only well-formed entries
    out: dict[str, dict[str, float | str]] = {}
    for ticker, meta in cfg.items():
        if not isinstance(meta, dict):
            continue
        try:
            w = float(meta.get("weight", 0))
        except (TypeError, ValueError):
            continue
        if w <= 0:
            continue
        out[str(ticker)] = {
            "name": str(meta.get("name", ticker)),
            "weight": w,
            "kind": str(meta.get("kind", "")),
        }
    return out or DEFAULT_BASKET


def apply_defensive_hedge(
    target: PortfolioTarget,
    regime: MarketRegime | str | None = None,
    *,
    nav: float | None = None,
) -> PortfolioTarget:
    """Shift up to ``hedge.max_hedge_weight`` of cash into the defensive basket.

    Mutates the target in place and returns it. No-op when:
        * hedge.enabled is false (default false)
        * regime is risk_on
        * cash_weight is below hedge.min_cash_to_hedge
    """
    if not bool(get_setting("hedge.enabled", False)):
        return target

    regime_str = (
        regime.value if isinstance(regime, MarketRegime) else str(regime or "neutral")
    )
    if regime_str == "risk_on":
        return target

    max_hedge = float(get_setting("hedge.max_hedge_weight", 0.20))
    min_cash = float(get_setting("hedge.min_cash_to_hedge", 0.10))
    if target.cash_weight < min_cash:
        return target

    # Tier the allocation: neutral → half max, risk_off → full max.
    bucket_pct = max_hedge if regime_str == "risk_off" else max_hedge * 0.5
    bucket_pct = min(bucket_pct, target.cash_weight - min_cash)
    if bucket_pct <= 0:
        return target

    basket = _basket()
    total_w = sum(float(m["weight"]) for m in basket.values()) or 1.0

    for ticker, meta in basket.items():
        share = (float(meta["weight"]) / total_w) * bucket_pct
        target.positions[ticker] = round(
            target.positions.get(ticker, 0.0) + share, 6
        )

    target.cash_weight = round(target.cash_weight - bucket_pct, 6)
    target.rationale = (
        f"{target.rationale} | hedge {bucket_pct:.1%} via "
        f"{','.join(basket.keys())} (regime={regime_str})"
    )
    log.info(
        "hedge.applied",
        bucket_pct=round(bucket_pct, 4),
        regime=regime_str,
        tickers=list(basket.keys()),
    )
    return target
