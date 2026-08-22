"""Pytest configuration — adds src/ to the import path."""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# `tests/` is a package, so pytest puts the repo root on the path rather than this
# directory — add it explicitly so shared helpers like `upbit_fakes` import cleanly.
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
