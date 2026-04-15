"""Every agent's dry-mode stub must pass its own validator."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agents import (
    ExecutorAgent,
    MacroAgent,
    QuantAgent,
    ReflectionAgent,
    ResearchAgent,
    SectorAgent,
    ValueAgent,
)
from src.agents.base import AgentContext


@pytest.fixture
def ctx() -> AgentContext:
    return AgentContext(
        run_id="test",
        as_of=datetime.now(UTC),
        universe=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    )


@pytest.mark.parametrize(
    "agent_cls",
    [ResearchAgent, MacroAgent, SectorAgent, ValueAgent, QuantAgent, ExecutorAgent, ReflectionAgent],
)
async def test_agent_stub_runs_and_validates(agent_cls, ctx: AgentContext) -> None:
    agent = agent_cls()
    result = await agent.run(ctx)
    assert result.agent == agent.name
    assert result.payload  # non-empty
