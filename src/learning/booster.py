"""ML alpha booster — auto-tune consensus weights from attribution history.

The system already journals every PICK with the agent that produced it,
the conviction, and a forward-return outcome (1w / 1m / 3m, backfilled by
``memory.outcomes``). This module:

    1. Pulls every PICK row that has a 1m outcome (label).
    2. Builds a feature matrix per agent: (conviction, score) -> 1m return.
    3. Trains a tiny ridge-regression model per agent → predicted alpha.
    4. Aggregates the models into a *consensus weight nudge* — agents whose
       picks reliably outperform get a bigger share, those who don't get
       trimmed.

Output is bounded: each weight moves at most ±5% per cycle, total weights
re-normalised to 1.0. Operators can apply via reflection_apply (recommended)
or call ``propose_consensus_nudges`` directly to inspect.

If sklearn isn't installed we fall back to plain mean-of-outcomes per agent
so the booster still works, just less smartly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from common.config import get_setting
from common.logging import get_logger

log = get_logger(__name__)


@dataclass
class AgentScore:
    agent: str
    n: int
    mean_outcome_1m: float
    pred_alpha: float


@dataclass
class WeightNudge:
    agent: str
    old_weight: float
    new_weight: float
    delta: float


def extract_training_data(lookback_days: int = 180) -> dict[str, list[dict]]:
    """Pull labelled PICK rows per agent. Returns ``{agent: [row, ...]}``.

    Each row carries: ``{conviction, score, outcome_1m, ts, ticker}``.
    """
    from memory.journal import DecisionJournal

    j = DecisionJournal()
    rows = j.recent(lookback_days)
    out: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("action") != "PICK":
            continue
        if r.get("outcome_1m") is None:
            continue
        ctx_raw = r.get("context_json")
        try:
            ctx = json.loads(ctx_raw) if isinstance(ctx_raw, str) else (ctx_raw or {})
        except Exception:
            ctx = {}
        out.setdefault(r["agent"], []).append(
            {
                "conviction": float(r.get("conviction") or 0),
                "score": float(ctx.get("score") or 0),
                "outcome_1m": float(r.get("outcome_1m")),
                "ts": r.get("ts"),
                "ticker": r.get("ticker"),
            }
        )
    return out


def train_agent_quality_model(
    rows: list[dict],
) -> tuple[float, list[float]]:
    """Tiny ridge regression: outcome_1m ~ a + b1*conviction + b2*score.

    Returns ``(predicted_alpha_at_avg_features, raw_coeffs)``. When sklearn
    is missing, falls back to the mean outcome.
    """
    if not rows:
        return 0.0, []

    try:
        import numpy as np
        from sklearn.linear_model import Ridge

        feat = np.array([[r["conviction"], r["score"]] for r in rows], dtype=float)
        y = np.array([r["outcome_1m"] for r in rows], dtype=float)
        if len(rows) < 5 or np.std(y) == 0:
            return float(y.mean()), []
        model = Ridge(alpha=1.0).fit(feat, y)
        # Evaluate at the median feature point — typical agent behaviour
        med = np.median(feat, axis=0)
        pred = float(model.predict(med.reshape(1, -1))[0])
        return pred, list(map(float, model.coef_))
    except Exception as e:
        log.debug("booster.sklearn_unavailable", error=str(e))
        # Plain mean fallback
        return sum(r["outcome_1m"] for r in rows) / len(rows), []


def score_agents(lookback_days: int = 180) -> list[AgentScore]:
    """Train + score each agent. Sorted by predicted alpha, descending."""
    data = extract_training_data(lookback_days)
    out: list[AgentScore] = []
    for agent, rows in data.items():
        pred, _ = train_agent_quality_model(rows)
        mean_out = sum(r["outcome_1m"] for r in rows) / len(rows) if rows else 0.0
        out.append(
            AgentScore(
                agent=agent,
                n=len(rows),
                mean_outcome_1m=mean_out,
                pred_alpha=pred,
            )
        )
    out.sort(key=lambda s: s.pred_alpha, reverse=True)
    return out


def propose_consensus_nudges(
    *,
    max_delta: float = 0.05,
    min_samples: int = 20,
) -> list[WeightNudge]:
    """Translate per-agent alpha into ``consensus.weights`` adjustments.

    Algorithm:
        1. Score each specialist agent with score_agents().
        2. Reject agents with < ``min_samples`` labelled picks (noisy).
        3. Re-rank weights so agents with higher pred_alpha get more share,
           bounded by ``max_delta`` per cycle.
        4. Normalise to sum 1.0.

    Output is a list of WeightNudge — caller decides whether to apply them
    (via ``reflection_apply`` patches) or just notify.
    """
    scores = score_agents()
    eligible = [s for s in scores if s.n >= min_samples]
    if not eligible:
        return []

    current = dict(get_setting("consensus.weights", {}) or {})
    if not current:
        return []

    # Map each eligible agent to a softmax-ish score
    pa = {s.agent: s.pred_alpha for s in eligible}
    # Translate alpha to relative score: positive shifts up, negative down.
    target_share = {}
    base_total = sum(current.values()) or 1.0
    for agent, w in current.items():
        if agent not in pa:
            target_share[agent] = w / base_total
            continue
        # Tilt: every +1% predicted 1m alpha → +5% relative share boost
        tilt = 1.0 + 5.0 * pa[agent]
        target_share[agent] = (w / base_total) * max(tilt, 0.1)

    norm = sum(target_share.values()) or 1.0
    raw_new = {a: s / norm for a, s in target_share.items()}

    # Clamp per-agent delta
    bounded: dict[str, float] = {}
    for agent, old in current.items():
        old_n = old / base_total
        new_n = raw_new.get(agent, old_n)
        delta = new_n - old_n
        if delta > max_delta:
            delta = max_delta
        elif delta < -max_delta:
            delta = -max_delta
        bounded[agent] = old_n + delta

    # Re-normalise
    final_norm = sum(bounded.values()) or 1.0
    nudges: list[WeightNudge] = []
    for agent, old in current.items():
        old_n = old / base_total
        new_final = bounded[agent] / final_norm
        nudges.append(
            WeightNudge(
                agent=agent,
                old_weight=round(old_n, 4),
                new_weight=round(new_final, 4),
                delta=round(new_final - old_n, 4),
            )
        )
    return nudges
