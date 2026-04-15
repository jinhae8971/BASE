"""LLM abstraction layer.

We wrap every model call behind a narrow `LLMClient` interface so that:
  - tests can inject a `MockLLMClient` that returns canned payloads without
    network or API keys,
  - the real runtime uses `AnthropicLLMClient` which lazily imports the
    `anthropic` SDK and uses tool-calling to guarantee structured JSON output,
  - prompt caching is centralized (system prompt + stable shared context get
    `cache_control: ephemeral` markers so subsequent agent calls in the same
    run hit the 5-minute prompt cache),
  - cost tracking and a daily USD budget guard live in one place.

The `call_tool` contract: force the model to call exactly one tool whose
`input_schema` matches the agent's expected JSON. Return the tool input dict.
"""

from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Protocol

from src.config import get_settings
from src.logging import get_logger

log = get_logger("llm")


# ---------------------------------------------------------------------------
# Pricing (USD per million tokens). Approximate; update when Anthropic changes
# pricing. The exact values matter less than the order of magnitude for the
# daily budget guard.
# ---------------------------------------------------------------------------
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6": {
        "input": 15.0,
        "cache_write": 18.75,
        "cache_read": 1.50,
        "output": 75.0,
    },
    "claude-sonnet-4-6": {
        "input": 3.0,
        "cache_write": 3.75,
        "cache_read": 0.30,
        "output": 15.0,
    },
}


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    p = PRICING.get(model) or PRICING["claude-sonnet-4-6"]
    # `input_tokens` from the SDK is the *non-cached* portion; cache_read and
    # cache_write are reported separately and should not be double-counted.
    return (
        input_tokens * p["input"]
        + cache_read_tokens * p["cache_read"]
        + cache_write_tokens * p["cache_write"]
        + output_tokens * p["output"]
    ) / 1_000_000


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SystemBlock:
    """A block of system prompt. `cacheable=True` adds an ephemeral cache
    breakpoint after this block so Anthropic serves subsequent identical
    prefixes from the prompt cache (5-min TTL)."""

    text: str
    cacheable: bool = False


@dataclass
class LLMResult:
    payload: dict[str, Any]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


class BudgetExceeded(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Cost tracker: one shared instance per process. Resets at UTC midnight.
# ---------------------------------------------------------------------------


@dataclass
class CostTracker:
    day: str = ""
    cost_usd: float = 0.0
    calls: int = 0

    def _rollover(self, now: datetime) -> None:
        today = now.strftime("%Y-%m-%d")
        if today != self.day:
            self.day = today
            self.cost_usd = 0.0
            self.calls = 0

    def check_budget(self, now: datetime | None = None) -> None:
        settings = get_settings()
        now = now or datetime.now(UTC)
        self._rollover(now)
        if self.cost_usd >= settings.anthropic_daily_budget_usd:
            raise BudgetExceeded(
                f"daily LLM budget ${settings.anthropic_daily_budget_usd:.2f} "
                f"exhausted ({self.cost_usd:.4f} used, {self.calls} calls)"
            )

    def record(self, cost: float, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        self._rollover(now)
        self.cost_usd += cost
        self.calls += 1


_tracker = CostTracker()


def tracker() -> CostTracker:
    return _tracker


# ---------------------------------------------------------------------------
# LLMClient interface
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    async def call_tool(
        self,
        *,
        model: str,
        system: list[SystemBlock],
        user: str,
        tool_name: str,
        tool_schema: dict[str, Any],
        max_tokens: int = 2048,
    ) -> LLMResult: ...


# ---------------------------------------------------------------------------
# Mock -- for tests and dry mode
# ---------------------------------------------------------------------------


@dataclass
class MockCall:
    model: str
    system: list[SystemBlock]
    user: str
    tool_name: str
    tool_schema: dict[str, Any]


class MockLLMClient:
    """Returns canned payloads. Tests register per-tool-name handlers."""

    def __init__(
        self,
        handlers: dict[str, Callable[[MockCall], dict[str, Any]]] | None = None,
    ) -> None:
        self.handlers = handlers or {}
        self.calls: list[MockCall] = []

    def register(
        self, tool_name: str, handler: Callable[[MockCall], dict[str, Any]]
    ) -> None:
        self.handlers[tool_name] = handler

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
        call = MockCall(model, list(system), user, tool_name, tool_schema)
        self.calls.append(call)
        handler = self.handlers.get(tool_name)
        if handler is None:
            raise KeyError(f"MockLLMClient: no handler for tool {tool_name!r}")
        payload = handler(call)
        # Pretend we used 100 in + 50 out tokens per call so cost tracking
        # exercises the same path as the real client.
        result = LLMResult(
            payload=payload,
            model=model,
            input_tokens=100,
            output_tokens=50,
            cost_usd=estimate_cost(model, 100, 50),
        )
        _tracker.record(result.cost_usd)
        return result


# ---------------------------------------------------------------------------
# Anthropic -- real runtime
# ---------------------------------------------------------------------------


class AnthropicLLMClient:
    """Thin wrapper around the async Anthropic SDK.

    Imports `anthropic` lazily so the package is only required when the real
    client is actually instantiated. Tests never touch this class.
    """

    def __init__(self, api_key: str | None = None) -> None:
        from anthropic import AsyncAnthropic  # lazy import

        key = api_key or get_settings().anthropic_api_key
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for AnthropicLLMClient")
        self._client = AsyncAnthropic(api_key=key)

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
        _tracker.check_budget()

        system_blocks: list[dict[str, Any]] = []
        for block in system:
            entry: dict[str, Any] = {"type": "text", "text": block.text}
            if block.cacheable:
                entry["cache_control"] = {"type": "ephemeral"}
            system_blocks.append(entry)

        start = datetime.now(UTC)
        resp = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": user}],
            tools=[
                {
                    "name": tool_name,
                    "description": f"Emit the {tool_name} payload as structured JSON.",
                    "input_schema": tool_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name, "disable_parallel_tool_use": True},
        )
        latency = int((datetime.now(UTC) - start).total_seconds() * 1000)

        payload = _extract_tool_input(resp, tool_name)

        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        cost = estimate_cost(model, in_tok, out_tok, cache_read, cache_write)
        _tracker.record(cost)

        log.info(
            "llm.call",
            model=model,
            tool=tool_name,
            in_tok=in_tok,
            out_tok=out_tok,
            cache_read=cache_read,
            cost=round(cost, 6),
            latency_ms=latency,
        )

        return LLMResult(
            payload=payload,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost,
            latency_ms=latency,
        )


def _extract_tool_input(resp: Any, tool_name: str) -> dict[str, Any]:
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return dict(block.input)
    raise RuntimeError(f"Anthropic response did not include tool_use for {tool_name}")


# ---------------------------------------------------------------------------
# Factory used by the orchestrator
# ---------------------------------------------------------------------------


def default_client() -> LLMClient:
    """Return the right client for the current trading mode.

    - dry: a MockLLMClient with no handlers registered (agents must use stubs)
    - paper/live: AnthropicLLMClient
    """
    from src.config import TradingMode

    mode = get_settings().trading_mode
    if mode == TradingMode.DRY:
        return MockLLMClient()
    return AnthropicLLMClient()
