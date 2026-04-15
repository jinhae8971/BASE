"""LLM layer tests -- mock client, cost tracker, budget guard, agent path."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agents import ResearchAgent
from src.agents.base import AgentContext
from src.llm import (
    BudgetExceeded,
    CostTracker,
    MockCall,
    MockLLMClient,
    SystemBlock,
    estimate_cost,
    tracker,
)


def test_estimate_cost_sonnet_matches_pricing_table() -> None:
    # 1M input + 1M output tokens on Sonnet -> 3 + 15 = $18
    assert estimate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000) == pytest.approx(18.0)


def test_estimate_cost_counts_cache_read_and_write_separately() -> None:
    # 100K input, 100K cache_read, 100K cache_write, 100K output on Opus
    # = 100K*(15 + 1.5 + 18.75 + 75) / 1M = 11.025
    cost = estimate_cost(
        "claude-opus-4-6",
        input_tokens=100_000,
        output_tokens=100_000,
        cache_read_tokens=100_000,
        cache_write_tokens=100_000,
    )
    assert cost == pytest.approx(11.025)


def test_cost_tracker_rolls_over_on_new_day() -> None:
    t = CostTracker(day="2025-01-01", cost_usd=3.0, calls=5)
    # Pretend today is a new day.
    t._rollover(datetime(2025, 1, 2, tzinfo=UTC))
    assert t.cost_usd == 0.0
    assert t.calls == 0
    assert t.day == "2025-01-02"


def test_cost_tracker_raises_when_budget_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_DAILY_BUDGET_USD", "1.0")
    from src.config import get_settings
    get_settings.cache_clear()  # type: ignore[attr-defined]

    t = CostTracker(day=datetime.now(UTC).strftime("%Y-%m-%d"), cost_usd=1.5)
    with pytest.raises(BudgetExceeded):
        t.check_budget()


async def test_mock_llm_returns_registered_payload_and_records_cost() -> None:
    initial_calls = tracker().calls

    def handler(call: MockCall) -> dict:
        assert call.tool_name == "emit_research"
        return {
            "coins": {sym: {"sentiment": 0.3, "narrative_strength": 0.6, "risk_flags": []}
                      for sym in ["BTCUSDT", "ETHUSDT"]},
            "top_narratives": ["AI"],
        }

    mock = MockLLMClient(handlers={"emit_research": handler})
    agent = ResearchAgent(llm_client=mock)
    ctx = AgentContext(
        run_id="t", as_of=datetime.now(UTC),
        universe=["BTCUSDT", "ETHUSDT"],
    )
    result = await agent.run(ctx)
    assert result.agent == "research"
    assert result.payload["top_narratives"] == ["AI"]
    assert result.cost_usd > 0
    assert tracker().calls > initial_calls
    # And the mock captured the call.
    assert len(mock.calls) == 1
    assert mock.calls[0].tool_name == "emit_research"
    # System prompt block was marked cacheable.
    assert mock.calls[0].system[0].cacheable is True


async def test_agent_raises_if_llm_payload_violates_schema() -> None:
    def bad_handler(call: MockCall) -> dict:
        return {"coins": {"BTCUSDT": {"sentiment": 2.0, "narrative_strength": 0.5, "risk_flags": []}},
                "top_narratives": []}

    agent = ResearchAgent(llm_client=MockLLMClient(handlers={"emit_research": bad_handler}))
    ctx = AgentContext(run_id="t", as_of=datetime.now(UTC), universe=["BTCUSDT"])
    with pytest.raises(ValueError, match="sentiment out of range"):
        await agent.run(ctx)


async def test_agent_still_uses_stub_when_no_client_injected() -> None:
    agent = ResearchAgent()  # no llm_client
    ctx = AgentContext(run_id="t", as_of=datetime.now(UTC), universe=["BTCUSDT"])
    result = await agent.run(ctx)
    assert result.payload["coins"]["BTCUSDT"]["sentiment"] == 0.0
    assert result.cost_usd == 0.0
