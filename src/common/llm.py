"""Anthropic Claude client wrapper with prompt caching support."""
from __future__ import annotations

from typing import Any

from anthropic import Anthropic

from .config import get_env


def get_claude_client() -> Anthropic:
    env = get_env()
    if not env.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return Anthropic(api_key=env.anthropic_api_key)


def call_claude(
    system: str | list[dict[str, Any]],
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    max_tokens: int = 4000,
    temperature: float = 0.2,
    cache_system: bool = True,
) -> str:
    """Minimal text completion helper with optional system-prompt caching.

    The system prompt is passed as a list of content blocks so we can attach
    `cache_control: {"type": "ephemeral"}` for prompt caching.
    """
    client = get_claude_client()
    env = get_env()
    model = model or env.claude_reasoning_model

    if isinstance(system, str):
        sys_blocks: list[dict[str, Any]] = [{"type": "text", "text": system}]
        if cache_system:
            sys_blocks[0]["cache_control"] = {"type": "ephemeral"}
    else:
        sys_blocks = system

    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=sys_blocks,
        messages=messages,
    )
    # Concatenate text blocks
    parts: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts)
