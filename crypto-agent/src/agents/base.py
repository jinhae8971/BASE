"""Base class for all specialist agents.

Phase 2 upgrade: every agent now declares a system prompt (loaded from
`src/prompts/<name>.md`), a tool schema (JSON Schema) that constrains its
output, and a `user_message()` method that serializes the relevant slice of
the shared `AgentContext`. The `_run_llm` implementation then calls the
injected `LLMClient` via the tool-calling API.

Modes:
  - `TradingMode.DRY` without an injected `llm_client` -> `_run_stub`
    returns the deterministic mock payload (Phase 0 behavior, used for
    wiring tests).
  - Any other mode OR an injected `llm_client` -> `_run_llm` executes the
    real tool-calling flow (works with both `AnthropicLLMClient` for prod
    and `MockLLMClient` for unit tests).

Both paths funnel through `_validate()` so schema violations are caught
whether the output came from a stub, a mock, or the real model.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.config import TradingMode, get_settings
from src.llm import LLMClient, SystemBlock
from src.logging import get_logger
from src.prompts import load as load_prompt


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
    cost_usd: float = 0.0
    latency_ms: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class BaseAgent(abc.ABC):
    #: Short identifier used in logs, trade journal, ELO table, prompt file.
    name: str = "base"
    #: Which model family this agent uses: "fast" -> Sonnet, "strong" -> Opus.
    model_key: str = "fast"
    #: Max output tokens for the tool call. Agents that produce big JSON
    #: (executor, reflection) override this upward.
    max_tokens: int = 2048

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.log = get_logger(f"agent.{self.name}")
        self.settings = get_settings()
        self._llm = llm_client

    # --- subclass contract --------------------------------------------

    @property
    @abc.abstractmethod
    def tool_name(self) -> str: ...

    @property
    @abc.abstractmethod
    def input_schema(self) -> dict[str, Any]: ...

    @abc.abstractmethod
    def user_message(self, ctx: AgentContext) -> str:
        """Serialize the relevant slice of context as the user prompt."""

    @abc.abstractmethod
    def _run_stub(self, ctx: AgentContext) -> dict[str, Any]:
        """Deterministic payload used in dry mode without an LLM client."""

    @abc.abstractmethod
    def _validate(self, payload: dict[str, Any]) -> None:
        """Raise if payload does not conform to this agent's schema."""

    # --- shared behavior ----------------------------------------------

    @property
    def model(self) -> str:
        return (
            self.settings.agent_model_strong
            if self.model_key == "strong"
            else self.settings.agent_model_fast
        )

    def system_blocks(self, ctx: AgentContext) -> list[SystemBlock]:
        """Default: one large cacheable block = the static prompt file.

        Agents can override to add per-run cacheable shared context (e.g. the
        MarketSnapshot summary common to multiple agents in the same run).
        """
        prompt = load_prompt(self.name)
        return [SystemBlock(text=prompt, cacheable=True)]

    async def run(self, ctx: AgentContext) -> AgentResult:
        start = datetime.now(UTC)
        use_stub = self._should_use_stub()
        if use_stub:
            payload = self._run_stub(ctx)
            cost = 0.0
            in_tok = out_tok = cache_read = 0
        else:
            assert self._llm is not None
            result = await self._llm.call_tool(
                model=self.model,
                system=self.system_blocks(ctx),
                user=self.user_message(ctx),
                tool_name=self.tool_name,
                tool_schema=self.input_schema,
                max_tokens=self.max_tokens,
            )
            payload = result.payload
            cost = result.cost_usd
            in_tok = result.input_tokens
            out_tok = result.output_tokens
            cache_read = result.cache_read_tokens

        latency = int((datetime.now(UTC) - start).total_seconds() * 1000)
        self._validate(payload)
        self.log.info(
            "agent.done",
            stub=use_stub,
            latency_ms=latency,
            cost_usd=round(cost, 6),
            in_tok=in_tok,
            cache_read=cache_read,
        )
        return AgentResult(
            agent=self.name,
            run_id=ctx.run_id,
            payload=payload,
            model=self.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_tokens=cache_read,
            cost_usd=cost,
            latency_ms=latency,
        )

    def _should_use_stub(self) -> bool:
        """Use the stub if we're in dry mode AND no client was injected.

        Tests that want to exercise the LLM path inject a MockLLMClient and
        leave mode=DRY — in that case we still take the LLM branch because
        the presence of a client is treated as the stronger signal.
        """
        if self._llm is not None:
            return False
        return self.settings.trading_mode == TradingMode.DRY
