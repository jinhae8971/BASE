"""Manual kill switch.

The orchestrator checks for a file named `HALT` at the repo root before every
order batch. If present, all trading is blocked and an alert is emitted. The
file must be removed manually (human ack) to resume trading.
"""

from __future__ import annotations

from pathlib import Path

HALT_FILE = Path("HALT")


def is_halted(root: Path | None = None) -> bool:
    base = root or Path.cwd()
    return (base / HALT_FILE).exists()


def engage(reason: str, root: Path | None = None) -> None:
    base = root or Path.cwd()
    (base / HALT_FILE).write_text(reason + "\n", encoding="utf-8")
