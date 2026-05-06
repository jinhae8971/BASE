"""Persist daily pipeline state between phases.

The 08:00 research phase computes the day's target and writes it to
``$MAIS_DATA_DIR/state/target_<YYYY-MM-DD>.json`` (plus a ``latest_target.json``
symlink/copy). The 09:05 order phase reads it back so we don't waste 4 LLM
calls re-running every agent if the market hasn't moved meaningfully.

If the day's target file is missing (e.g. system was off at 08:00), the order
phase falls back to running research synchronously.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from common.config import get_env
from common.logging import get_logger
from common.types import PortfolioTarget

log = get_logger(__name__)


def _state_dir() -> Path:
    p = Path(get_env().mais_data_dir) / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_target(target: PortfolioTarget, *, extra: dict[str, Any] | None = None) -> Path:
    payload = {
        "target": target.model_dump(mode="json"),
        "extra": extra or {},
    }
    p = _state_dir() / f"target_{target.as_of.isoformat()}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = _state_dir() / "latest_target.json"
    latest.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    log.info("state.target_saved", path=str(p))
    return p


def load_target(when: date) -> tuple[PortfolioTarget, dict[str, Any]] | None:
    p = _state_dir() / f"target_{when.isoformat()}.json"
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        target = PortfolioTarget.model_validate(payload["target"])
        return target, payload.get("extra", {})
    except Exception as e:
        log.warning("state.target_load_failed", error=str(e))
        return None
