"""Redis cache helper. Phase 1 will wire this into every data client."""

from __future__ import annotations

import json
from typing import Any

DEFAULT_TTL = 300


async def get(key: str) -> Any | None:  # pragma: no cover - Phase 1
    return None


async def set(key: str, value: Any, ttl: int = DEFAULT_TTL) -> None:  # pragma: no cover
    _ = json.dumps(value)  # validate serializability early
