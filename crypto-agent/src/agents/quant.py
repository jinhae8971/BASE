"""Crypto Quant agent -- price/volatility signals.

Builds a compact per-symbol feature summary from the snapshot's candles so
the LLM doesn't have to ingest raw OHLCV. The features are deterministic
numerics (momentum, volatility, distance-from-extremes) the model can reason
about without having to do any numerical computation itself.
"""

from __future__ import annotations

import json
import math
from typing import Any

from src.agents.base import AgentContext, BaseAgent


class QuantAgent(BaseAgent):
    name = "quant"
    model_key = "fast"

    @property
    def tool_name(self) -> str:
        return "emit_quant"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["coins", "vol_regime"],
            "properties": {
                "coins": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "required": ["signal", "vol_target", "stop_pct"],
                        "properties": {
                            "signal": {"type": "number", "minimum": -1, "maximum": 1},
                            "vol_target": {"type": "number", "exclusiveMinimum": 0, "maximum": 0.2},
                            "stop_pct": {"type": "number", "minimum": 0, "maximum": 50},
                        },
                    },
                },
                "vol_regime": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "crisis"],
                },
            },
        }

    def user_message(self, ctx: AgentContext) -> str:
        candles_by_sym: dict[str, list[dict[str, Any]]] = (
            ctx.market_data.get("candles", {}) or {}
        )
        features: dict[str, dict[str, float]] = {}
        for sym in ctx.universe:
            candles = candles_by_sym.get(sym) or []
            features[sym] = _summarize(candles)
        return json.dumps({"universe": ctx.universe, "features": features})

    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        return {
            "coins": {
                sym: {"signal": 0.0, "vol_target": 0.02, "stop_pct": 8.0}
                for sym in ctx.universe
            },
            "vol_regime": "normal",
        }

    def _validate(self, payload: dict[str, Any]) -> None:
        if "coins" not in payload:
            raise ValueError("quant: missing coins")
        for sym, row in payload["coins"].items():
            if not -1.0 <= float(row["signal"]) <= 1.0:
                raise ValueError(f"quant: {sym} signal out of range")
            if float(row["vol_target"]) <= 0:
                raise ValueError(f"quant: {sym} vol_target must be positive")


def _summarize(candles: list[dict[str, Any]]) -> dict[str, float]:
    if not candles:
        return {
            "last_close": 0.0,
            "chg_24h": 0.0,
            "chg_7d": 0.0,
            "chg_30d": 0.0,
            "vol_20d": 0.0,
            "mom_20d": 0.0,
            "dist_high_20d": 0.0,
            "dist_low_20d": 0.0,
        }
    closes = [float(c["c"]) for c in candles]
    last = closes[-1]

    def _pct(back: int) -> float:
        if len(closes) < back + 1 or closes[-(back + 1)] == 0:
            return 0.0
        return 100.0 * (last / closes[-(back + 1)] - 1.0)

    # Realized vol & momentum over last 20 daily returns.
    rets: list[float] = []
    for i in range(1, min(21, len(closes))):
        if closes[-i - 1] == 0:
            continue
        rets.append(closes[-i] / closes[-i - 1] - 1.0)
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        stdev = math.sqrt(max(var, 0.0))
        mom = (mean / stdev) if stdev > 0 else 0.0
    else:
        stdev = 0.0
        mom = 0.0

    window = closes[-20:]
    high20 = max(window)
    low20 = min(window) or 1e-9
    dist_high = 100.0 * (last / high20 - 1.0) if high20 else 0.0
    dist_low = 100.0 * (last / low20 - 1.0)

    return {
        "last_close": last,
        "chg_24h": _pct(1),
        "chg_7d": _pct(7),
        "chg_30d": _pct(30),
        "vol_20d": round(stdev, 6),
        "mom_20d": round(mom, 4),
        "dist_high_20d": round(dist_high, 4),
        "dist_low_20d": round(dist_low, 4),
    }
