"""Heuristic LLM client -- deterministic baseline for backtests.

Implements the `LLMClient` protocol but computes agent payloads with simple,
reproducible rules over the same user message the real agents would receive.
This gives the backtest a meaningful "beat BTC with basic rules" baseline
without burning Anthropic credits, and it gives the test suite a way to
drive DailyWorkflow with non-trivial signals.

The rules are intentionally simple -- momentum-driven long-only tilt, core
BTC/ETH floor, macro cash floor -- so that anything the LLM does can be
compared against this as an Alpha-vs-heuristic benchmark.
"""

from __future__ import annotations

import json
from typing import Any

from src.llm import LLMResult, SystemBlock, estimate_cost, tracker


class HeuristicLLMClient:
    """Deterministic LLMClient implementation used by the backtest.

    The return shapes match each agent's tool schema exactly, so the
    BaseAgent validators still run and catch any drift.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(
        self,
        *,
        model: str,
        system: list[SystemBlock],
        user: str,
        tool_name: str,
        tool_schema: dict[str, Any],
        max_tokens: int = 2048,
    ) -> LLMResult:
        self.calls += 1
        try:
            user_obj = json.loads(user) if user else {}
        except json.JSONDecodeError:
            user_obj = {}

        if tool_name == "emit_research":
            payload = _research(user_obj)
        elif tool_name == "emit_macro":
            payload = _macro(user_obj)
        elif tool_name == "emit_sector":
            payload = _sector(user_obj)
        elif tool_name == "emit_value":
            payload = _value(user_obj)
        elif tool_name == "emit_quant":
            payload = _quant(user_obj)
        elif tool_name == "emit_executor":
            payload = _executor(user_obj)
        elif tool_name == "emit_reflection":
            payload = _reflection(user_obj)
        else:
            raise KeyError(f"HeuristicLLMClient: no rule for tool {tool_name!r}")

        cost = estimate_cost(model, 1, 1)  # nominal
        tracker().record(cost)
        return LLMResult(payload=payload, model=model, input_tokens=1, output_tokens=1, cost_usd=cost)


# ---------------------------------------------------------------------------
# Per-agent rules
# ---------------------------------------------------------------------------


def _research(user: dict[str, Any]) -> dict[str, Any]:
    universe = user.get("universe", [])
    markets = {m["s"]: m for m in user.get("markets", [])}
    coins: dict[str, dict[str, Any]] = {}
    for sym in universe:
        base = sym[:-4] if sym.endswith("USDT") else sym
        m = markets.get(base, {})
        chg7 = float(m.get("c7d", 0.0) or 0.0)
        # Sentiment as a bounded function of recent return.
        sentiment = max(-1.0, min(1.0, chg7 / 20.0))
        coins[sym] = {
            "sentiment": round(sentiment, 4),
            "narrative_strength": 0.3,
            "risk_flags": [],
        }
    return {"coins": coins, "top_narratives": []}


def _macro(user: dict[str, Any]) -> dict[str, Any]:
    fred = user.get("fred", {})
    vix = float(fred.get("VIX", 0.0) or 0.0)
    if vix == 0.0:
        return {
            "regime": "neutral",
            "btc_bias": 0.0,
            "leverage_cap": 0.7,
            "cash_floor_pct": 20.0,
            "notes": "heuristic: no macro data available",
        }
    if vix < 15:
        return {
            "regime": "risk-on", "btc_bias": 0.4, "leverage_cap": 0.9,
            "cash_floor_pct": 10.0, "notes": "low VIX -> risk on",
        }
    if vix > 25:
        return {
            "regime": "risk-off", "btc_bias": -0.3, "leverage_cap": 0.3,
            "cash_floor_pct": 50.0, "notes": "high VIX -> risk off",
        }
    return {
        "regime": "neutral", "btc_bias": 0.0, "leverage_cap": 0.7,
        "cash_floor_pct": 25.0, "notes": "mid VIX -> neutral",
    }


_SECTOR_KEYS = ["L1", "L2", "DeFi", "AI", "RWA", "Gaming", "Meme"]


def _sector(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "sector_scores": {k: 0.0 for k in _SECTOR_KEYS},
        "hot_sectors": [],
        "rotation_signal": "none",
    }


def _value(user: dict[str, Any]) -> dict[str, Any]:
    universe = user.get("universe", [])
    markets = {m["symbol"]: m for m in user.get("markets", [])}
    coins: dict[str, dict[str, Any]] = {}
    for sym in universe:
        base = sym[:-4] if sym.endswith("USDT") else sym
        m = markets.get(base, {})
        mc = float(m.get("market_cap", 0.0) or 0.0)
        fdv = float(m.get("fdv", 0.0) or mc)
        # FDV / MCAP > 2 is dilution risk; prefer <= 1.2.
        ratio = (fdv / mc) if mc > 0 else 1.0
        conviction = 0.4 if ratio <= 1.2 else -0.2
        coins[sym] = {
            "fair_value_ratio": 1.0,
            "conviction": conviction,
            "horizon_days": 180,
        }
    return {"coins": coins}


def _quant(user: dict[str, Any]) -> dict[str, Any]:
    universe = user.get("universe", [])
    features = user.get("features", {})
    coins: dict[str, dict[str, Any]] = {}
    for sym in universe:
        f = features.get(sym, {})
        mom = float(f.get("mom_20d", 0.0) or 0.0)
        vol = float(f.get("vol_20d", 0.02) or 0.02)
        # Trend-following: positive momentum => long, saturate at +/- 1.
        signal = max(-1.0, min(1.0, mom / 0.5))
        # Narrow stops for thin vol, wider otherwise.
        stop = max(5.0, min(15.0, vol * 400))
        coins[sym] = {
            "signal": round(signal, 4),
            "vol_target": round(max(0.005, min(0.2, vol)), 6),
            "stop_pct": round(stop, 2),
        }
    return {"coins": coins, "vol_regime": "normal"}


def _executor(user: dict[str, Any]) -> dict[str, Any]:
    """Rebalance to the optimizer's target weights.

    The orchestrator already handed us `portfolio.target_weights` (from
    `optimize()`), so the executor's job here is mechanical: compute the
    rebalance delta vs the currently-held weights and emit the orders that
    would move the portfolio onto target. We do NOT second-guess the
    optimizer -- that's the LLM's job; the heuristic baseline just trusts it.
    """
    universe: list[str] = user.get("universe", [])
    portfolio: dict[str, Any] = user.get("portfolio", {}) or {}
    target_weights: dict[str, float] = portfolio.get("target_weights", {}) or {}
    cash_pct: float = float(portfolio.get("cash_pct", 100.0))
    current_weights: dict[str, float] = portfolio.get("current_weights", {}) or {}
    equity: float = float(portfolio.get("equity_usd", 1000.0) or 1000.0)

    orders = []
    for sym in universe:
        target = float(target_weights.get(sym, 0.0))
        current = float(current_weights.get(sym, 0.0)) * 100.0
        delta_pct = target - current
        if abs(delta_pct) < 3.0:  # dead band: avoid churn below 3pp
            continue
        qty_usd = abs(delta_pct) / 100.0 * equity
        # Cap per-order at 30% of equity (same as risk guardrail).
        qty_usd = min(qty_usd, equity * 0.30)
        if qty_usd < 5.0:
            continue
        orders.append(
            {
                "symbol": sym,
                "side": "BUY" if delta_pct > 0 else "SELL",
                "qty_usd": round(qty_usd, 2),
                "type": "MARKET",
                "reason": f"rebalance {delta_pct:+.1f}pp",
            }
        )

    return {
        "target_weights": target_weights,
        "cash_pct": cash_pct,
        "orders": orders,
        "rationale": "heuristic rebalance to optimizer target",
    }


def _reflection(user: dict[str, Any]) -> dict[str, Any]:
    """Assign credit / blame to each signal agent based on whether its entry
    prediction agreed with the realized outcome.

    For each agent we pull the entry-time payload out of `agent_decisions`,
    extract a scalar "was this bullish" signal in [-1, 1], and score it as
    +|signal|*sign(pnl) when both move together, -|signal|*|sign(pnl)| when
    they diverge. Zero-signal agents get a zero delta -- no credit, no
    blame, they simply had no opinion.

    This is a *baseline*. A real Reflection LLM reads the narrative
    context, weighs the regime, and can credit the value agent for a call
    that took 60 days to play out. The heuristic is deliberately simpler.
    """
    pnl = float(user.get("realized_pnl_pct", 0.0) or 0.0)
    regime = str(user.get("macro_regime_at_entry", "unknown"))
    symbol = str(user.get("symbol", "?"))
    verdict = "won" if pnl > 0 else ("lost" if pnl < 0 else "flat")
    pnl_sign = 1.0 if pnl > 0 else (-1.0 if pnl < 0 else 0.0)

    decisions: dict[str, Any] = user.get("agent_decisions", {}) or {}

    def _scalar(name: str) -> float:
        pay = decisions.get(name) or {}
        if name == "research":
            coins = pay.get("coins") or {}
            row = coins.get(symbol) or {}
            return float(row.get("sentiment", 0.0) or 0.0)
        if name == "macro":
            return float(pay.get("btc_bias", 0.0) or 0.0)
        if name == "value":
            coins = pay.get("coins") or {}
            row = coins.get(symbol) or {}
            return float(row.get("conviction", 0.0) or 0.0)
        if name == "quant":
            coins = pay.get("coins") or {}
            row = coins.get(symbol) or {}
            return float(row.get("signal", 0.0) or 0.0)
        if name == "sector":
            return 0.0  # sector is portfolio-level, not per-coin
        return 0.0

    scores: dict[str, float] = {}
    for a in ("research", "macro", "sector", "value", "quant"):
        s = _scalar(a)
        # Agreement with outcome: sign(s) * sign(pnl) * |s|, bounded in [-1,1].
        agreement = max(-1.0, min(1.0, (s * pnl_sign) if s != 0 else 0.0))
        scores[a] = round(agreement, 4)
    # The executor gets credit proportional to realized pnl -- it's the one
    # who actually sized and timed the trade.
    scores["executor"] = max(-1.0, min(1.0, pnl / 20.0))

    return {
        "lesson": (
            f"{symbol} {verdict} {pnl:+.2f}% in {regime} regime. Heuristic "
            f"attribution: {', '.join(f'{k}={v:+.2f}' for k, v in scores.items())}."
        ),
        "agent_scores": scores,
        "tags": [regime, verdict],
    }
