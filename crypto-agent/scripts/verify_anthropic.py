"""Manual smoke test: run one DailyWorkflow with real Anthropic calls.

The entire test suite runs with a `MockLLMClient`, so this is the only
place the actual Anthropic SDK gets exercised. It's intentionally NOT a
pytest — it requires a real API key and burns real credits (bounded by
`ANTHROPIC_DAILY_BUDGET_USD`). Run it manually when:

  - You've added or edited a prompt in `src/prompts/` and want to see
    what the model actually returns before shipping it.
  - You want to validate prompt-cache hit rates against the pricing
    table and see concrete per-agent token counts.
  - You're about to promote to paper/live and want a final sanity check
    that the agents produce valid JSON against the real model.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python -m scripts.verify_anthropic

The script forces `TRADING_MODE=dry` regardless of env so no orders can
leave the process — only the LLM path is exercised. The result prints
per-agent token counts + cumulative cost.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

from src.agents.base import AgentContext
from src.agents.executor import ExecutorAgent
from src.agents.macro import MacroAgent
from src.agents.quant import QuantAgent
from src.agents.research import ResearchAgent
from src.agents.sector import SectorAgent
from src.agents.value import ValueAgent
from src.llm import AnthropicLLMClient, tracker


def _fake_context() -> AgentContext:
    """Minimal but non-empty context so the agents have something to reason about."""
    now = datetime.now(UTC)
    universe = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    return AgentContext(
        run_id="anthropic-verify",
        as_of=now,
        universe=universe,
        market_data={
            "markets": [
                {"symbol": "BTC", "market_cap": 1.2e12, "fdv": 1.26e12,
                 "volume_24h": 5e10, "chg24h": 1.5, "chg7d": 4.2},
                {"symbol": "ETH", "market_cap": 4.0e11, "fdv": 4.0e11,
                 "volume_24h": 2e10, "chg24h": 0.8, "chg7d": 2.5},
                {"symbol": "SOL", "market_cap": 7.0e10, "fdv": 8.2e10,
                 "volume_24h": 3e9, "chg24h": -0.5, "chg7d": 5.1},
            ],
            "candles": {
                sym: [
                    {"t": now.isoformat(), "o": 100, "h": 101, "l": 99,
                     "c": 100 + i * 0.1, "v": 1000, "qv": 100_000}
                    for i in range(30)
                ]
                for sym in universe
            },
            "categories": [
                {"id": "l1", "name": "Layer 1", "market_cap": 1.5e12, "volume_24h": 6e10},
            ],
        },
        macro_data={
            "fred": {"10Y": 4.25, "2Y": 4.80, "DXY": 104.3, "VIX": 14.0,
                     "CPI": 307.0, "FFR": 5.33},
            "as_of": {"10Y": "2025-01-01"},
        },
        onchain_data={
            "chain_tvl": [
                {"name": "Ethereum", "tvl": 5e10, "chg1d": 0.5, "chg7d": 2.0},
                {"name": "Solana", "tvl": 5e9, "chg1d": 1.2, "chg7d": 8.0},
            ],
        },
        news=[
            {"title": "Fed signals patient approach to further cuts",
             "currencies": ["BTC"], "votes": {}, "published_at": now.isoformat()},
        ],
    )


async def main_async() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("ERROR: set ANTHROPIC_API_KEY to run this script.")
        return 2

    # Force dry so no orders leave the process even though we're running real LLM.
    os.environ["TRADING_MODE"] = "dry"

    llm = AnthropicLLMClient(api_key=key)
    ctx = _fake_context()

    agents = [
        ("research", ResearchAgent(llm_client=llm)),
        ("macro", MacroAgent(llm_client=llm)),
        ("sector", SectorAgent(llm_client=llm)),
        ("value", ValueAgent(llm_client=llm)),
        ("quant", QuantAgent(llm_client=llm)),
        ("executor", ExecutorAgent(llm_client=llm)),
    ]

    print(f"Running {len(agents)} agents against real Anthropic API ...")
    print()
    rows: list[dict[str, Any]] = []
    total_cost = 0.0

    for name, agent in agents:
        t0 = datetime.now(UTC)
        try:
            result = await agent.run(ctx)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:10s}  FAILED: {exc}")
            continue
        dt_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)
        rows.append(
            {
                "agent": name,
                "model": result.model,
                "in_tokens": result.input_tokens,
                "cached_tokens": result.cached_tokens,
                "out_tokens": result.output_tokens,
                "cost_usd": round(result.cost_usd, 6),
                "latency_ms": dt_ms,
            }
        )
        total_cost += result.cost_usd
        print(
            f"  {name:10s}  {result.model:20s}  "
            f"in={result.input_tokens:5d}  cached={result.cached_tokens:5d}  "
            f"out={result.output_tokens:4d}  ${result.cost_usd:.6f}  {dt_ms}ms"
        )

    print()
    print(f"Total cost: ${total_cost:.6f}")
    print(f"CostTracker daily total: ${tracker().cost_usd:.6f} ({tracker().calls} calls)")
    print()
    print("Sample agent output payloads:")
    for row in rows:
        print(f"  {row['agent']:10s}  {row['out_tokens']} out tokens")
    print()
    print(json.dumps(rows, indent=2))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
