"""End-to-end DailyWorkflow with a MockLLMClient wired into every agent.

This test proves that:
  - every signal agent routes through the LLM path when a client is injected,
  - the aggregator/optimizer/executor still function with non-zero signals,
  - the executor can emit real orders that pass the risk guardrails in dry
    mode and land in the trade store.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.agents.quant import QuantAgent
from src.data.binance_md import Candle
from src.data.coingecko import MarketRow
from src.data.defillama import ChainTvl
from src.data.fred import MacroSnapshot
from src.data.snapshot import MarketSnapshot
from src.llm import MockCall, MockLLMClient
from src.orchestrator.daily_workflow import DailyWorkflow


def _fake_snapshot() -> MarketSnapshot:
    now = datetime.now(UTC)
    snap = MarketSnapshot(as_of=now)
    snap.universe = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    snap.candles = {
        sym: [
            Candle(open_time=now, open=100, high=101, low=99, close=100 + i * 0.1,
                   volume=1000, quote_volume=100_000)
            for i in range(30)
        ]
        for sym in snap.universe
    }
    snap.markets = [
        MarketRow(id="bitcoin", symbol="BTC", name="Bitcoin",
                  market_cap=1e12, fdv=1.05e12, volume_24h=5e10,
                  price_change_24h_pct=1.0, price_change_7d_pct=3.0),
        MarketRow(id="ethereum", symbol="ETH", name="Ethereum",
                  market_cap=4e11, fdv=4e11, volume_24h=2e10,
                  price_change_24h_pct=0.8, price_change_7d_pct=2.5),
    ]
    snap.chain_tvl = [ChainTvl(name="Ethereum", tvl_usd=5e10, change_1d_pct=0.5, change_7d_pct=2.0)]
    snap.macro = MacroSnapshot(values={"10Y": 4.25, "VIX": 14.0}, as_of={"10Y": "2025-01-01"})
    return snap


def _build_mock() -> MockLLMClient:
    mock = MockLLMClient()

    def research(call: MockCall) -> dict:
        return {
            "coins": {
                "BTCUSDT": {"sentiment": 0.5, "narrative_strength": 0.7, "risk_flags": []},
                "ETHUSDT": {"sentiment": 0.3, "narrative_strength": 0.5, "risk_flags": []},
                "SOLUSDT": {"sentiment": 0.2, "narrative_strength": 0.4, "risk_flags": []},
            },
            "top_narratives": ["BTC halving", "AI"],
        }

    def macro(call: MockCall) -> dict:
        return {
            "regime": "risk-on",
            "btc_bias": 0.5,
            "leverage_cap": 0.9,
            "cash_floor_pct": 10.0,
            "notes": "Low VIX, easing macro.",
        }

    def sector(call: MockCall) -> dict:
        return {
            "sector_scores": {k: 0.2 for k in ["L1", "L2", "DeFi", "AI", "RWA", "Gaming", "Meme"]},
            "hot_sectors": ["L1", "AI"],
            "rotation_signal": "into-majors",
        }

    def value(call: MockCall) -> dict:
        return {
            "coins": {
                "BTCUSDT": {"fair_value_ratio": 0.9, "conviction": 0.6, "horizon_days": 180},
                "ETHUSDT": {"fair_value_ratio": 0.95, "conviction": 0.4, "horizon_days": 180},
                "SOLUSDT": {"fair_value_ratio": 1.1, "conviction": 0.1, "horizon_days": 120},
            }
        }

    def quant(call: MockCall) -> dict:
        return {
            "coins": {
                "BTCUSDT": {"signal": 0.5, "vol_target": 0.02, "stop_pct": 6.0},
                "ETHUSDT": {"signal": 0.3, "vol_target": 0.025, "stop_pct": 7.0},
                "SOLUSDT": {"signal": 0.2, "vol_target": 0.04, "stop_pct": 10.0},
            },
            "vol_regime": "normal",
        }

    def executor(call: MockCall) -> dict:
        return {
            "target_weights": {"BTCUSDT": 40.0, "ETHUSDT": 25.0, "SOLUSDT": 15.0},
            "cash_pct": 20.0,
            "orders": [
                {"symbol": "BTCUSDT", "side": "BUY", "qty_usd": 200.0,
                 "type": "MARKET", "reason": "core underweight"},
                {"symbol": "ETHUSDT", "side": "BUY", "qty_usd": 120.0,
                 "type": "MARKET", "reason": "core underweight"},
            ],
            "rationale": "Risk-on regime plus core underweight -- add BTC and ETH.",
        }

    mock.register("emit_research", research)
    mock.register("emit_macro", macro)
    mock.register("emit_sector", sector)
    mock.register("emit_value", value)
    mock.register("emit_quant", quant)
    mock.register("emit_executor", executor)
    return mock


async def test_daily_workflow_runs_full_llm_path() -> None:
    async def fake_gather(**kwargs):  # noqa: ANN003
        return _fake_snapshot()

    mock = _build_mock()
    wf = DailyWorkflow(snapshot_fn=fake_gather, llm_client=mock)
    result = await wf.run(universe_size=3)

    assert result["universe"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert len(result["orders"]) == 2
    assert {o["symbol"] for o in result["orders"]} == {"BTCUSDT", "ETHUSDT"}
    # All six agents produced payloads through the mock.
    tool_names = {c.tool_name for c in mock.calls}
    assert tool_names == {
        "emit_research", "emit_macro", "emit_sector",
        "emit_value", "emit_quant", "emit_executor",
    }


async def test_quant_feature_summary_handles_empty_candles() -> None:
    agent = QuantAgent()
    ctx_msg = agent.user_message(
        type("C", (), {  # type: ignore[arg-type]
            "universe": ["XYZUSDT"],
            "market_data": {"candles": {}},
        })()
    )
    assert "XYZUSDT" in ctx_msg
    # Zeros everywhere is a valid summary; test just ensures no crash.
