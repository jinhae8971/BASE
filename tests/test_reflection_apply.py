"""Reflection auto-apply must enforce the whitelist + safety bounds."""
from __future__ import annotations

from pathlib import Path

import yaml


def _setup(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    from common import config as c

    c.get_env.cache_clear()
    c.load_yaml_settings.cache_clear()

    # Point the module's settings.yaml writer at a temp file
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        yaml.safe_dump(
            {
                "consensus": {
                    "weights": {
                        "macro": 0.20,
                        "sector": 0.15,
                        "value": 0.20,
                        "quant": 0.45,
                    }
                },
                "risk": {"hard_stop_pct": 0.12, "trailing_take_pct": 0.10},
                "memory": {"journal_db": str(tmp_path / "j.sqlite")},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    from agents import reflection_apply as ra

    monkeypatch.setattr(ra, "_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {},
    )
    return settings_path


def _report(text: str, tmp_path: Path) -> Path:
    p = tmp_path / "reflections" / "2025-05-09.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_apply_within_bounds(monkeypatch, tmp_path) -> None:
    settings_path = _setup(monkeypatch, tmp_path)
    report = _report(
        """
# Reflection 2025-05-09

## Auto-apply patches
```json
[
  {"path": "consensus.weights.quant", "value": 0.48,
   "rationale": "quant outperformed by 2% last month"},
  {"path": "risk.hard_stop_pct", "value": 0.10,
   "rationale": "tighter stops in low-vol regime"}
]
```
""",
        tmp_path,
    )
    from agents.reflection_apply import apply_reflection_patches

    out = apply_reflection_patches(report)
    assert len(out["applied"]) == 2
    after = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    assert after["consensus"]["weights"]["quant"] == 0.48
    assert after["risk"]["hard_stop_pct"] == 0.10


def test_reject_outside_bounds(monkeypatch, tmp_path) -> None:
    settings_path = _setup(monkeypatch, tmp_path)
    report = _report(
        """
## Auto-apply patches
```json
[
  {"path": "risk.hard_stop_pct", "value": 0.50, "rationale": "way too loose"}
]
```
""",
        tmp_path,
    )
    from agents.reflection_apply import apply_reflection_patches

    out = apply_reflection_patches(report)
    assert out["applied"] == []
    assert out["rejected"][0]["reason"].startswith("value 0.5 outside bounds")
    after = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    # Original value preserved
    assert after["risk"]["hard_stop_pct"] == 0.12


def test_reject_oversized_delta(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path)
    report = _report(
        """
## Auto-apply patches
```json
[
  {"path": "consensus.weights.quant", "value": 0.65,
   "rationale": "huge swing"}
]
```
""",
        tmp_path,
    )
    from agents.reflection_apply import apply_reflection_patches

    out = apply_reflection_patches(report)
    assert out["applied"] == []
    assert "delta" in out["rejected"][0]["reason"]


def test_reject_non_whitelisted_path(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path)
    report = _report(
        """
## Auto-apply patches
```json
[
  {"path": "broker.api_url", "value": "evil"}
]
```
""",
        tmp_path,
    )
    from agents.reflection_apply import apply_reflection_patches

    out = apply_reflection_patches(report)
    assert out["applied"] == []
    assert "not whitelisted" in out["rejected"][0]["reason"]


def test_no_patches_block_returns_skipped(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path)
    report = _report("# Reflection\n\nJust prose, no JSON block.", tmp_path)
    from agents.reflection_apply import apply_reflection_patches

    out = apply_reflection_patches(report)
    assert out["_skipped"] == "no_patches_block"
