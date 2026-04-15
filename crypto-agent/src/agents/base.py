"""Base class for all specialist agents.

An agent takes a typed `AgentContext` (snapshot of inputs gathered by the
orchestrator) and returns an `AgentResult` whose `payload` conforms to a
strict JSON schema. In dry mode, `_run_stub` returns a deterministic mock so
the whole pipeline can be wired end-to-end before any Anthropic API call.

LLM calls (Phase 2) will use Anthropic prompt caching: the system prompt and
the stable universe definition are marked as cache breakpoints so repeated
daily runs pay cache-read pricing.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.config import TradingMode, get_settings
from src.logging import get_logger


@dataclass
class AgentContext:
    """Snapshot of inputs handed to every agent for a single daily run."""

    run_id: str
    as_of: datetime
    universe: list[str]
    market_data: dict[str, Any] = field(default_factory=dict)
    macro_data: dict[str, Any] = field(default_factory=dict)
    onchain_data: dict[str, Any] = field(default_factory=dict)
    news: list[dict[str, Any]] = field(default_factory=list)
    portfolio: dict[str, Any] = field(default_factory=dict)
    lessons: list[str] = field(default_factory=list)  # RAG-injected from reflection


@dataclass
class AgentResult:
    agent: str
    run_id: str
    payload: dict[str, Any]
    model: str
    cached_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class BaseAgent(abc.ABC):
    """Template-method pattern: subclasses define prompt, schema, and stub."""

    #: Short identifier used in logs, trade journal, ELO table.
    name: str = "base"
    #: Which model this agent uses. Overridden per subclass.
    model_key: str = "fast"  # "fast" | "strong"

    def __init__(self) -> None:
        self.log = get_logger(f"agent.{self.name}")
        self.settings = get_settings()

    @property
    def model(self) -> str:
        return (
            self.settings.agent_model_strong
            if self.model_key == "strong"
            else self.settings.agent_model_fast
        )

    async def run(self, ctx: AgentContext) -> AgentResult:
        start = datetime.now(UTC)
        if self.settings.trading_mode == TradingMode.DRY:
            payload = self._run_stub(ctx)
        else:
            payload = await self._run_llm(ctx)
        latency = int((datetime.now(UTC) - start).total_seconds() * 1000)
        self._validate(payload)
        self.log.info("agent.done", latency_ms=latency, keys=list(payload.keys()))
        return AgentResult(
            agent=self.name,
            run_id=ctx.run_id,
            payload=payload,
            model=self.model,
            latency_ms=latency,
        )

    # --- subclass hooks ------------------------------------------------

    @abc.abstractmethod
    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        """Deterministic mock used in dry mode."""

    async def _run_llm(self, ctx: AgentContext) -> dict[str, Any]:
        """Real Anthropic call. Implemented in Phase 2."""
        raise NotImplementedError("LLM path is implemented in Phase 2")

    @abc.abstractmethod
    def _validate(self, payload: dict[str, Any]) -> None:
        """Raise if payload does not conform to this agent's schema."""
