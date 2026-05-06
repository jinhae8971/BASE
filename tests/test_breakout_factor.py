from __future__ import annotations

from agents.quant_agent import REGIME_WEIGHTS


def test_breakout_factor_in_all_regimes() -> None:
    for regime, weights in REGIME_WEIGHTS.items():
        assert "breakout" in weights, f"missing breakout in {regime}"
    # risk_on should weight breakout >= 0.10 (clear momentum bias)
    assert REGIME_WEIGHTS["risk_on"]["breakout"] >= 0.10
    # risk_off should drop breakout near zero
    assert REGIME_WEIGHTS["risk_off"]["breakout"] <= 0.05


def test_weights_still_sum_to_one() -> None:
    for regime, w in REGIME_WEIGHTS.items():
        total = sum(w.values())
        assert abs(total - 1.0) < 1e-6, f"{regime}: {total}"
