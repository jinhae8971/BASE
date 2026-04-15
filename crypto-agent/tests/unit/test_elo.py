from __future__ import annotations

from src.learning.elo import EloTable


def test_default_weights_sum_to_one() -> None:
    table = EloTable()
    weights = table.weights()
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_update_from_scores_shifts_weights_toward_winner() -> None:
    table = EloTable()
    before = table.weights()["quant"]
    table.update_from_scores(
        {"research": 0.0, "macro": 0.0, "sector": 0.0, "value": 0.0, "quant": 2.0}
    )
    after = table.weights()["quant"]
    assert after > before
