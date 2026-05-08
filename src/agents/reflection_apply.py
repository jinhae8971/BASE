"""Apply selective Reflection-proposed parameter changes automatically.

The ReflectionAgent writes a Markdown report; if that report ends with a
fenced ``json`` block under the heading ``Auto-apply patches`` we parse it
and apply *whitelisted* settings.yaml mutations, bounded by safety limits.

Anything outside the whitelist is journaled but **never** applied — those
require human review.

Whitelist:
    consensus.weights.{macro,sector,value,quant}    delta <= 0.05 absolute
    risk.hard_stop_pct                               in [0.05, 0.20]
    risk.trailing_take_pct                           in [0.05, 0.20]
    risk.cash_buffer_min                             in [0.02, 0.20]
    execution.rebalance_buy_threshold                in [0.02, 0.10]
    execution.rebalance_sell_threshold               in [0.04, 0.15]

Each accepted change is journaled (agent='reflection_apply') with the
old/new value and the reflection report path.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from common.config import get_env, load_yaml_settings
from common.logging import get_logger
from common.notifications import notify_info, notify_warning

log = get_logger(__name__)

# (path, lower bound, upper bound, max abs delta per apply)
_WHITELIST: dict[str, tuple[float, float, float]] = {
    "consensus.weights.macro": (0.0, 0.6, 0.05),
    "consensus.weights.sector": (0.0, 0.6, 0.05),
    "consensus.weights.value": (0.0, 0.6, 0.05),
    "consensus.weights.quant": (0.0, 0.7, 0.05),
    "risk.hard_stop_pct": (0.05, 0.20, 0.03),
    "risk.trailing_take_pct": (0.05, 0.20, 0.03),
    "risk.cash_buffer_min": (0.02, 0.20, 0.05),
    "execution.rebalance_buy_threshold": (0.02, 0.10, 0.02),
    "execution.rebalance_sell_threshold": (0.04, 0.15, 0.03),
}

_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"


def _extract_patches(report_text: str) -> list[dict[str, Any]]:
    """Pull a JSON list out of a fenced block following 'Auto-apply patches'."""
    pattern = re.compile(
        r"##\s*Auto-apply patches\s*\n+```json\s*\n(.*?)\n```",
        re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(report_text)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        log.warning("reflection_apply.parse_failed", error=str(e))
        return []
    if not isinstance(data, list):
        return []
    return data


def _get_nested(d: dict, dotted: str) -> Any:
    cur: Any = d
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _set_nested(d: dict, dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def _validate(path: str, new_value: float, current: float | None) -> str | None:
    if path not in _WHITELIST:
        return f"path '{path}' not whitelisted"
    lo, hi, max_delta = _WHITELIST[path]
    try:
        nv = float(new_value)
    except (TypeError, ValueError):
        return f"non-numeric value: {new_value!r}"
    if nv < lo or nv > hi:
        return f"value {nv} outside bounds [{lo}, {hi}]"
    if current is not None and abs(nv - float(current)) > max_delta:
        return f"delta {nv - current:+.4f} exceeds max {max_delta}"
    return None


def apply_reflection_patches(report_path: str | Path) -> dict[str, Any]:
    """Read a reflection report, parse + apply whitelisted patches.

    Returns ``{"applied": [...], "rejected": [...]}``.
    """
    report_path = Path(report_path)
    if not report_path.exists():
        return {"applied": [], "rejected": [], "_skipped": "no_report"}

    text = report_path.read_text(encoding="utf-8")
    patches = _extract_patches(text)
    if not patches:
        return {"applied": [], "rejected": [], "_skipped": "no_patches_block"}

    settings = load_yaml_settings()
    # Work on a deep copy so a partial failure doesn't leave settings half-applied
    import copy

    candidate = copy.deepcopy(settings)

    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for p in patches:
        path = str(p.get("path", ""))
        new_v = p.get("value")
        current = _get_nested(candidate, path)
        error = _validate(path, new_v, current)
        if error:
            rejected.append({"path": path, "value": new_v, "reason": error})
            log.warning("reflection_apply.rejected", path=path, value=new_v, reason=error)
            continue
        _set_nested(candidate, path, float(new_v))
        applied.append(
            {
                "path": path,
                "old": current,
                "new": float(new_v),
                "rationale": str(p.get("rationale", ""))[:200],
            }
        )

    if applied:
        # Write back to settings.yaml + journal each change
        with _SETTINGS_PATH.open("w", encoding="utf-8") as f:
            yaml.safe_dump(candidate, f, allow_unicode=True, sort_keys=False)
        # Bust caches so the running scheduler reads the new values
        try:
            from common import config as c

            c.load_yaml_settings.cache_clear()
        except Exception:
            pass

        from memory.journal import DecisionJournal

        journal = DecisionJournal()
        for change in applied:
            journal.record(
                agent="reflection_apply",
                action="PARAM_UPDATE",
                ticker=None,
                conviction=10,
                rationale=change["rationale"],
                context={
                    "path": change["path"],
                    "old": change["old"],
                    "new": change["new"],
                    "report": str(report_path),
                    "ts": datetime.utcnow().isoformat(),
                },
            )
        notify_info(
            "Reflection auto-apply",
            f"{len(applied)} parameter changes applied",
            paths=", ".join(c["path"] for c in applied),
        )
    if rejected:
        notify_warning(
            "Reflection rejected patches",
            f"{len(rejected)} patches blocked by safety bounds",
            sample=rejected[0]["reason"] if rejected else "",
        )

    log.info(
        "reflection_apply.done",
        applied=len(applied),
        rejected=len(rejected),
        report=str(report_path),
    )
    return {
        "applied": applied,
        "rejected": rejected,
        "report": str(report_path),
    }


def apply_latest_reflection() -> dict[str, Any]:
    """Convenience: pick the most recent file under data_store/reflections/
    and apply it."""
    refl_dir = Path(get_env().mais_data_dir) / "reflections"
    if not refl_dir.exists():
        return {"applied": [], "rejected": [], "_skipped": "no_reflections_dir"}
    files = sorted(refl_dir.glob("*.md"), reverse=True)
    if not files:
        return {"applied": [], "rejected": [], "_skipped": "no_reflections"}
    return apply_reflection_patches(files[0])
