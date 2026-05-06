from __future__ import annotations

from agents.quant_agent import REGIME_WEIGHTS


def test_risk_on_momentum_dominates() -> None:
    w = REGIME_WEIGHTS["risk_on"]
    # Momentum + breakout together should dominate (the trend bucket)
    assert w["momentum"] + w["breakout"] >= 0.45
    # Trend bucket + size + flow should be the bulk of the bull-market allocation
    assert w["momentum"] + w["breakout"] + w["size"] + w["flow"] >= 0.6
    # Quality + lowvol should be light in a risk_on book
    assert w["quality"] + w["lowvol"] <= 0.3


def test_risk_off_defensive() -> None:
    w = REGIME_WEIGHTS["risk_off"]
    # Quality + low-vol take over in a defensive book
    assert w["quality"] + w["lowvol"] >= 0.5
    # Momentum should be heavily reduced
    assert w["momentum"] <= 0.15


def test_weights_sum_to_one() -> None:
    for regime, w in REGIME_WEIGHTS.items():
        total = sum(w.values())
        assert abs(total - 1.0) < 1e-6, f"{regime}: {total}"
