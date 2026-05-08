"""ε-greedy multi-armed bandit for TWAP slice scheduling.

Each arm is a candidate TWAP interval (seconds between child orders). At
the start of every order_phase the bandit picks an arm; at end-of-day we
score the day's fills (slippage + fill rate) and update the arm's reward.

Reward for an arm on day t:
    reward_t = -slippage_bps / 100   (lower slippage → higher reward)

State persists in ``$MAIS_DATA_DIR/bandit_twap.json``::

    {"arms": {"60": {"n": 12, "reward_sum": -3.2}, "180": {...}}, ...}

This is a *production* bandit — small, on-policy, cheap to compute. We
keep it deliberately simple so a malformed state file just resets the
exploration rather than crashing the loop.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.config import get_env, get_setting
from common.logging import get_logger

log = get_logger(__name__)

DEFAULT_ARMS_SECONDS: tuple[int, ...] = (60, 180, 300, 600)


def _state_path() -> Path:
    return Path(get_env().mais_data_dir) / "bandit_twap.json"


@dataclass
class ArmStats:
    n: int = 0
    reward_sum: float = 0.0

    @property
    def avg(self) -> float:
        return self.reward_sum / self.n if self.n > 0 else 0.0


@dataclass
class TWAPBandit:
    """ε-greedy bandit. Pickle-free, JSON-persisted."""

    arms: tuple[int, ...] = DEFAULT_ARMS_SECONDS
    epsilon: float = 0.1
    stats: dict[int, ArmStats] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for a in self.arms:
            self.stats.setdefault(a, ArmStats())

    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> TWAPBandit:
        epsilon = float(get_setting("execution.twap_bandit_epsilon", 0.1))
        arms_cfg = get_setting("execution.twap_bandit_arms", list(DEFAULT_ARMS_SECONDS))
        arms = tuple(int(a) for a in arms_cfg)
        bandit = cls(arms=arms, epsilon=epsilon)

        p = _state_path()
        if not p.exists():
            return bandit
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            arm_data = data.get("arms") or {}
            for k, v in arm_data.items():
                arm = int(k)
                if arm in bandit.stats:
                    bandit.stats[arm] = ArmStats(
                        n=int(v.get("n", 0)),
                        reward_sum=float(v.get("reward_sum", 0.0)),
                    )
        except Exception as e:
            log.warning("bandit.load_failed", error=str(e))
        return bandit

    def save(self) -> None:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(
                json.dumps(
                    {
                        "arms": {
                            str(a): {"n": s.n, "reward_sum": s.reward_sum}
                            for a, s in self.stats.items()
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning("bandit.save_failed", error=str(e))

    # ------------------------------------------------------------------
    def select(self) -> int:
        """ε-greedy: explore with prob ε, otherwise pick the best mean arm."""
        if not self.arms:
            return 60
        if random.random() < self.epsilon:
            return random.choice(self.arms)
        # Cold-start: untried arms first to ensure each gets a sample
        untried = [a for a in self.arms if self.stats[a].n == 0]
        if untried:
            return random.choice(untried)
        return max(self.arms, key=lambda a: self.stats[a].avg)

    def record(self, arm_seconds: int, reward: float) -> None:
        if arm_seconds not in self.stats:
            return
        s = self.stats[arm_seconds]
        s.n += 1
        s.reward_sum += float(reward)
        log.info(
            "bandit.update",
            arm=arm_seconds,
            n=s.n,
            avg_reward=round(s.avg, 4),
        )


# ----------------------------------------------------------------------
# Public helpers used by daily_pipeline
# ----------------------------------------------------------------------
def select_twap_interval() -> int:
    bandit = TWAPBandit.load()
    arm = bandit.select()
    log.info("bandit.selected", arm_seconds=arm)
    return arm


def record_day_outcome(arm_seconds: int, slippage_bps: float) -> None:
    """Score yesterday's choice. Lower slippage → higher reward."""
    bandit = TWAPBandit.load()
    reward = -float(slippage_bps) / 100.0
    bandit.record(arm_seconds, reward)
    bandit.save()


def latest_state() -> dict[str, Any]:
    """Snapshot for dashboard / morning report."""
    bandit = TWAPBandit.load()
    return {
        "epsilon": bandit.epsilon,
        "arms": [
            {"seconds": a, "n": bandit.stats[a].n, "avg_reward": bandit.stats[a].avg}
            for a in bandit.arms
        ],
    }
