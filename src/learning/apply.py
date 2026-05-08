"""Glue: turn booster nudges into a Reflection ``Auto-apply patches`` block.

Two ways to use it:

    1. Append the patches into the latest reflection report so the next
       eod_phase auto-apply picks them up (recommended — keeps human
       review possible).
    2. Call ``apply_now()`` to bypass reflection and apply directly to
       settings.yaml. Off by default; set ``learning.auto_apply: true``.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from common.config import get_env, get_setting
from common.logging import get_logger
from common.notifications import notify_info

from .booster import propose_consensus_nudges

log = get_logger(__name__)


def _build_patches(nudges: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in nudges:
        if abs(n.delta) < 0.005:
            continue
        out.append(
            {
                "path": f"consensus.weights.{n.agent}",
                "value": n.new_weight,
                "rationale": f"booster nudge {n.delta:+.3f} (1m alpha-driven)",
            }
        )
    return out


def append_to_latest_reflection() -> dict[str, Any]:
    """Append a patches block to the most recent reflection report."""
    nudges = propose_consensus_nudges()
    patches = _build_patches(nudges)
    if not patches:
        return {"_skipped": "no_significant_nudges", "nudges": []}

    refl_dir = Path(get_env().mais_data_dir) / "reflections"
    refl_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(refl_dir.glob("*.md"), reverse=True)
    if files:
        path = files[0]
    else:
        # No reflection yet — create a stub so the auto-apply path still runs
        path = refl_dir / f"{date.today().isoformat()}.md"
        path.write_text(
            "# Auto-generated reflection (booster only)\n\n", encoding="utf-8"
        )

    body = path.read_text(encoding="utf-8")
    if "## Auto-apply patches" in body:
        log.info("booster.skipped_existing_block", path=str(path))
        return {"_skipped": "patches_block_exists"}

    block = "\n## Auto-apply patches\n```json\n" + json.dumps(patches, indent=2) + "\n```\n"
    path.write_text(body + block, encoding="utf-8")
    notify_info(
        "Booster nudges queued",
        f"{len(patches)} weight tweaks pending eod auto-apply",
        report=path.name,
    )
    log.info("booster.appended", path=str(path), n=len(patches))
    return {"path": str(path), "patches": patches}


def apply_now() -> dict[str, Any]:
    """Skip reflection — apply directly. Gated by learning.auto_apply."""
    if not get_setting("learning.auto_apply", False):
        return {"_skipped": "learning.auto_apply=false"}
    nudges = propose_consensus_nudges()
    patches = _build_patches(nudges)
    if not patches:
        return {"_skipped": "no_significant_nudges"}
    # Reuse reflection_apply machinery — write a fake report file inline
    refl_dir = Path(get_env().mais_data_dir) / "reflections"
    refl_dir.mkdir(parents=True, exist_ok=True)
    p = refl_dir / f"booster_{date.today().isoformat()}.md"
    p.write_text(
        "# Booster auto-apply\n\n## Auto-apply patches\n```json\n"
        + json.dumps(patches, indent=2)
        + "\n```\n",
        encoding="utf-8",
    )
    from agents.reflection_apply import apply_reflection_patches

    return apply_reflection_patches(p)
