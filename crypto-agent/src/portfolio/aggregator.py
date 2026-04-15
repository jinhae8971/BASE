"""Aggregate specialist agent signals into a single score per symbol.

Weights come from `learning.elo`. If no ELO history exists yet we fall back to
uniform weights across the five signal-producing agents (executor is excluded
because it consumes this aggregate).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.agents.base import AgentResult

SIGNAL_AGENTS = ("research", "macro", "sector", "value", "quant")


@dataclass
class AggregatedSignal:
    symbol: str
    score: float          # -1..+1
    contributions: dict[str, float]


def aggregate(
    universe: list[str],
    results: dict[str, AgentResult],
    weights: dict[str, float] | None = None,
) -> dict[str, AggregatedSignal]:
    """Weighted blend of per-agent per-symbol signals.

    - `research.coins[sym].sentiment`
    - `macro.btc_bias` (broadcast to every symbol, scaled for non-BTC by 0.5)
    - `sector.sector_scores` (mapped via a naive placeholder until Phase 2)
    - `value.coins[sym].conviction`
    - `quant.coins[sym].signal`
    """
    w = weights or {a: 1.0 / len(SIGNAL_AGENTS) for a in SIGNAL_AGENTS}
    out: dict[str, AggregatedSignal] = {}

    research = results.get("research")
    macro = results.get("macro")
    value = results.get("value")
    quant = results.get("quant")

    for sym in universe:
        contrib: dict[str, float] = {}
        if research is not None:
            contrib["research"] = float(
                research.payload["coins"].get(sym, {}).get("sentiment", 0.0)
            )
        if macro is not None:
            bias = float(macro.payload.get("btc_bias", 0.0))
            contrib["macro"] = bias if sym.startswith("BTC") else 0.5 * bias
        # Sector mapping is deferred to Phase 2 — placeholder 0.
        contrib["sector"] = 0.0
        if value is not None:
            contrib["value"] = float(
                value.payload["coins"].get(sym, {}).get("conviction", 0.0)
            )
        if quant is not None:
            contrib["quant"] = float(
                quant.payload["coins"].get(sym, {}).get("signal", 0.0)
            )
        score = sum(w.get(a, 0.0) * v for a, v in contrib.items())
        out[sym] = AggregatedSignal(symbol=sym, score=score, contributions=contrib)
    return out
