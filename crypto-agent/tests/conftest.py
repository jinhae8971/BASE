"""Shared test fixtures. Force dry trading mode for all tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("TRADING_MODE", "dry")
os.environ.setdefault("ANTHROPIC_API_KEY", "")


@pytest.fixture(autouse=True)
def _redirect_filesystem_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets its own tmp RUNS_ROOT and ARCHIVE_ROOT so DailyWorkflow
    runs never write into the real `data/` directory. Tests that need to
    assert on the artifact path can still look up
    `src.orchestrator.run_artifact.RUNS_ROOT` after this fixture ran.
    """
    from src.data import archive as data_archive
    from src.orchestrator import run_artifact

    monkeypatch.setattr(run_artifact, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(data_archive, "ARCHIVE_ROOT", tmp_path / "archive")
