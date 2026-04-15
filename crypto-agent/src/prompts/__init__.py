"""Prompt template loading.

Prompts live as plain markdown files in this package so they are easy to
version, diff, and swap without touching Python. Loaded once and cached.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).parent


@lru_cache(maxsize=None)
def load(name: str) -> str:
    path = _HERE / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt {name!r} not found at {path}")
    return path.read_text(encoding="utf-8")
