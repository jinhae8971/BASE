"""End-to-end: DailyWorkflow with a synthetic MarketSnapshot injected."""

from __future__ import annotations

from datetime import UTC, datetime

from src.data.binance_md import Candle
from src.data.coingecko import MarketRow
from src.data.defillama import ChainTvl
from src.data.fred import MacroSnapshot
from src.data.snapshot import MarketSnapshot
from src.orchestrator.daily_workflow import DailyWorkflow


def _fake_snapshot() -> MarketSnapshot:
    now = datetime.now(UTC)
    snap = MarketSnapshot(as_of=now)
    snap.universe = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    snap.candles = {
        sym: [
            Candle(
                open_time=now, open=100, high=101, low=99, close=100.5,
                volume=1_000, quote_volume=100_000,
            )
        ]
        for sym in snap.universe
    }
    snap.markets = [
        MarketRow(id="bitcoin", symbol="BTC", name="Bitcoin",
                  market_cap=1e12, fdv=1.05e12, volume_24h=5e10,
                  price_change_24h_pct=1.0, price_change_7d_pct=3.0),
    ]
    snap.chain_tvl = [ChainTvl(name="Ethereum", tvl_usd=5e10, change_1d_pct=0.5, change_7d_pct=2.0)]
    snap.macro = MacroSnapshot(values={"10Y": 4.25}, as_of={"10Y": "2025-01-01"})
    return snap


async def test_workflow_runs_with_injected_snapshot() -> None:
    async def fake_gather(**kwargs):  # noqa: ANN003
        return _fake_snapshot()

    wf = DailyWorkflow(snapshot_fn=fake_gather)
    result = await wf.run(universe_size=3)
    assert result["universe"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert "allocation" in result
    assert result["orders"] == []  # executor stub emits none


async def test_workflow_halts_on_empty_universe() -> None:
    async def empty(**kwargs):  # noqa: ANN003
        return MarketSnapshot(as_of=datetime.now(UTC))

    wf = DailyWorkflow(snapshot_fn=empty)
    result = await wf.run()
    assert result.get("halted") is True
