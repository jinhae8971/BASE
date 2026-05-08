"""ε-greedy TWAP bandit — explore, exploit, persist."""
from __future__ import annotations

import random


def _patch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    from common import config as c

    c.get_env.cache_clear()
    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {
            "execution": {
                "twap_bandit_epsilon": 0.0,  # pure exploit for tests
                "twap_bandit_arms": [60, 180, 300, 600],
            }
        },
    )


def test_cold_start_explores_each_arm(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    from learning.bandit import TWAPBandit

    seen = set()
    random.seed(0)
    for _ in range(40):
        bandit = TWAPBandit.load()
        arm = bandit.select()
        seen.add(arm)
        bandit.record(arm, reward=-1.0)
        bandit.save()
    assert seen == {60, 180, 300, 600}


def test_exploit_picks_highest_avg(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    from learning.bandit import TWAPBandit

    bandit = TWAPBandit.load()
    bandit.record(60, -5.0)
    bandit.record(60, -4.0)
    bandit.record(180, -1.0)
    bandit.record(180, -0.5)
    bandit.record(300, -3.0)
    bandit.record(600, -2.0)
    bandit.save()

    fresh = TWAPBandit.load()
    # All arms have at least one observation, so cold-start path is done.
    # With ε=0 the best avg arm (180 → -0.75) should always be picked.
    arms = {fresh.select() for _ in range(20)}
    assert arms == {180}


def test_record_day_outcome_round_trip(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    from learning.bandit import latest_state, record_day_outcome

    record_day_outcome(180, slippage_bps=15)  # reward -0.15
    record_day_outcome(60, slippage_bps=40)   # reward -0.4
    state = latest_state()
    by_arm = {a["seconds"]: a for a in state["arms"]}
    assert by_arm[180]["n"] == 1
    assert abs(by_arm[180]["avg_reward"] - (-0.15)) < 1e-9
    assert by_arm[60]["n"] == 1
    assert abs(by_arm[60]["avg_reward"] - (-0.40)) < 1e-9


def test_corrupt_state_file_does_not_crash(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    (tmp_path / "bandit_twap.json").write_text("not-json", encoding="utf-8")
    from learning.bandit import TWAPBandit

    bandit = TWAPBandit.load()
    # All arms reset to zero — no crash, just clean state
    assert all(s.n == 0 for s in bandit.stats.values())
