"""MLP booster tier — verify the three-tier model selector.

These tests require sklearn (deep tier). They are skipped cleanly when
the package is not installed.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sklearn")


def _patch(monkeypatch, model: str = "auto") -> None:
    from common import config as c

    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {
            "learning": {"model": model, "mlp_hidden": [16, 8]},
        },
    )


def _rows(n: int, base: float = 0.02) -> list[dict]:
    return [
        {
            "conviction": 5 + (i % 5),
            "score": 1.0 + (i % 4) * 0.5,
            "outcome_1m": base + (i % 7) * 0.001,
        }
        for i in range(n)
    ]


def test_returns_mean_when_too_few_rows(monkeypatch) -> None:
    _patch(monkeypatch)
    from learning.booster import train_agent_quality_model

    pred, coeffs = train_agent_quality_model(_rows(3))
    assert coeffs == []
    assert isinstance(pred, float)


def test_ridge_path_for_medium_n(monkeypatch) -> None:
    _patch(monkeypatch, model="ridge")
    from learning.booster import train_agent_quality_model

    pred, coeffs = train_agent_quality_model(_rows(20))
    assert len(coeffs) == 4
    assert isinstance(pred, float)


def test_mlp_path_for_large_n(monkeypatch) -> None:
    _patch(monkeypatch, model="mlp")
    from learning.booster import train_agent_quality_model

    pred, coeffs = train_agent_quality_model(_rows(80))
    assert coeffs == []
    assert isinstance(pred, float)


def test_auto_falls_back_to_ridge_for_small_n(monkeypatch) -> None:
    _patch(monkeypatch, model="auto")
    from learning.booster import train_agent_quality_model

    pred, coeffs = train_agent_quality_model(_rows(15))
    assert len(coeffs) == 4
    assert isinstance(pred, float)


def test_zero_variance_outcomes_returns_mean(monkeypatch) -> None:
    _patch(monkeypatch)
    from learning.booster import train_agent_quality_model

    rows = [{"conviction": 7, "score": 1.5, "outcome_1m": 0.02} for _ in range(20)]
    pred, coeffs = train_agent_quality_model(rows)
    assert coeffs == []
    assert abs(pred - 0.02) < 1e-9
