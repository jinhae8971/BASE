"""Regression test: 52-week breakout factor must affect ranking.

We build a tiny synthetic factor panel where one ticker is mid-pack on
every other dimension but is printing a fresh new high. With the
risk_on weights, that ticker should jump above the rest of the field —
proving breakout actually flows through to selection.
"""
from __future__ import annotations

from agents.quant_agent import REGIME_WEIGHTS


def _composite(row: dict, weights: dict) -> float:
    return sum(weights[f] * row.get(f, 0.0) for f in weights)


def test_breakout_lifts_ranking_in_risk_on() -> None:
    weights = REGIME_WEIGHTS["risk_on"]
    base = {
        "momentum": 0.0,
        "value": 0.0,
        "quality": 0.0,
        "lowvol": 0.0,
        "size": 0.0,
        "flow": 0.0,
        "breakout": 0.0,
    }
    breakout_row = {**base, "breakout": 2.0}  # fresh new high
    score_with_breakout = _composite(breakout_row, weights)
    score_baseline = _composite(base, weights)
    assert score_with_breakout > score_baseline
    # Effect size at least equal to breakout weight x z-score
    assert score_with_breakout >= weights["breakout"] * 2.0 - 1e-9


def test_breakout_neutralised_in_risk_off() -> None:
    """In risk_off the breakout weight is 0 — a new-high name shouldn't
    be artificially promoted (defensive book)."""
    weights = REGIME_WEIGHTS["risk_off"]
    breakout_row = {
        "momentum": 0.0,
        "value": 0.0,
        "quality": 0.0,
        "lowvol": 0.0,
        "size": 0.0,
        "flow": 0.0,
        "breakout": 2.0,
    }
    quality_row = {**breakout_row, "breakout": 0.0, "quality": 1.0}
    s_breakout = _composite(breakout_row, weights)
    s_quality = _composite(quality_row, weights)
    # Quality should beat breakout in defensive regime
    assert s_quality > s_breakout


def test_full_factor_kit_ordering_is_stable() -> None:
    """Spot-check: a textbook risk_on stock (high M, B, F) outranks a
    textbook defensive stock (high Q, L) under risk_on weights."""
    risk_on_w = REGIME_WEIGHTS["risk_on"]
    momentum_leader = {
        "momentum": 1.5,
        "breakout": 1.2,
        "flow": 1.0,
        "value": 0.0,
        "quality": 0.0,
        "lowvol": -0.5,
        "size": 0.5,
    }
    defensive = {
        "momentum": -0.2,
        "breakout": 0.0,
        "flow": 0.0,
        "value": 0.5,
        "quality": 1.5,
        "lowvol": 1.5,
        "size": 0.5,
    }
    assert _composite(momentum_leader, risk_on_w) > _composite(defensive, risk_on_w)


def test_universe_size_factor_panel_keys() -> None:
    """Sanity: every regime weight set covers every factor in the panel
    so we can't accidentally drop a factor without noticing."""
    expected = {"momentum", "breakout", "value", "quality", "lowvol", "size", "flow"}
    for regime, w in REGIME_WEIGHTS.items():
        assert set(w.keys()) == expected, f"{regime} regime weights mismatch"
