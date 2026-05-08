"""Defensive hedge gates: enabled flag, regime, cash floor."""
from __future__ import annotations

from datetime import date

from common.types import MarketRegime, PortfolioTarget
from portfolio.hedge import DEFAULT_BASKET, apply_defensive_hedge


def _patch(monkeypatch, enabled: bool = True, max_w: float = 0.20, min_cash: float = 0.10) -> None:
    from common import config as c

    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {
            "hedge": {
                "enabled": enabled,
                "max_hedge_weight": max_w,
                "min_cash_to_hedge": min_cash,
            }
        },
    )


def _target(cash: float, positions: dict[str, float] | None = None) -> PortfolioTarget:
    return PortfolioTarget(
        as_of=date(2025, 5, 6),
        cash_weight=cash,
        positions=positions or {"005930": 0.4},
        rationale="base",
    )


def test_disabled_is_noop(monkeypatch) -> None:
    _patch(monkeypatch, enabled=False)
    t = _target(0.30)
    out = apply_defensive_hedge(t, MarketRegime.RISK_OFF)
    assert out.cash_weight == 0.30
    assert all(tk not in out.positions for tk in DEFAULT_BASKET)


def test_risk_on_is_noop(monkeypatch) -> None:
    _patch(monkeypatch)
    t = _target(0.30)
    out = apply_defensive_hedge(t, MarketRegime.RISK_ON)
    assert out.cash_weight == 0.30
    assert all(tk not in out.positions for tk in DEFAULT_BASKET)


def test_cash_below_floor_is_noop(monkeypatch) -> None:
    _patch(monkeypatch, min_cash=0.10)
    t = _target(0.05)
    out = apply_defensive_hedge(t, MarketRegime.RISK_OFF)
    assert out.cash_weight == 0.05


def test_risk_off_full_hedge(monkeypatch) -> None:
    _patch(monkeypatch, max_w=0.20, min_cash=0.05)
    t = _target(0.30)
    out = apply_defensive_hedge(t, MarketRegime.RISK_OFF)
    # Hedge bucket = max_w (0.20). cash drops by 0.20 → 0.10
    assert abs(out.cash_weight - 0.10) < 1e-6
    # All basket tickers populated, weights sum to 0.20
    assert all(tk in out.positions for tk in DEFAULT_BASKET)
    total_basket = sum(out.positions[tk] for tk in DEFAULT_BASKET)
    assert abs(total_basket - 0.20) < 1e-6


def test_neutral_half_hedge(monkeypatch) -> None:
    _patch(monkeypatch, max_w=0.20, min_cash=0.05)
    t = _target(0.30)
    out = apply_defensive_hedge(t, MarketRegime.NEUTRAL)
    # Neutral gets half the max → 0.10
    total_basket = sum(out.positions[tk] for tk in DEFAULT_BASKET)
    assert abs(total_basket - 0.10) < 1e-6
    assert abs(out.cash_weight - 0.20) < 1e-6


def test_basket_proportions(monkeypatch) -> None:
    """Per-ETF weight respects the basket ratios."""
    _patch(monkeypatch, max_w=0.20, min_cash=0.05)
    t = _target(0.30)
    out = apply_defensive_hedge(t, MarketRegime.RISK_OFF)
    # Gold has weight 0.50 in DEFAULT_BASKET → should be 0.10 (50% of 0.20)
    assert abs(out.positions["411060"] - 0.10) < 1e-6
    # Bonds 0.20 → 0.04
    assert abs(out.positions["132030"] - 0.04) < 1e-6
