"""run_backtest CLI tests — the synthetic-data path and the --real-llm guards.

The CLI glue is tested via `subprocess.run` on the current Python
interpreter so argparse + main_async + result-print formatting are all
exercised end-to-end. Real Anthropic calls are never made: the real-LLM
tests only hit the `--estimate-only` branch which doesn't instantiate
the Anthropic client at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


CRYPTO_AGENT_ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LOG_LEVEL"] = "ERROR"
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "scripts.run_backtest", *args],
        cwd=str(CRYPTO_AGENT_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_heuristic_synthetic_backtest_reports_metrics() -> None:
    result = _run("--days", "45")
    assert result.returncode == 0, result.stderr
    assert "Return" in result.stdout
    assert "BTC" in result.stdout
    assert "Alpha" in result.stdout
    assert '"num_orders"' in result.stdout


def test_real_llm_estimate_only_prints_cost_and_exits() -> None:
    result = _run(
        "--real-llm",
        "--estimate-only",
        "--days", "100",
        env_overrides={"ANTHROPIC_API_KEY": "placeholder-key"},
    )
    assert result.returncode == 0, result.stderr
    assert "REAL LLM BACKTEST" in result.stdout
    assert "per-day est" in result.stdout
    assert "total estimate" in result.stdout


def test_real_llm_estimate_only_honors_max_days_cap() -> None:
    # 60-day default cap in real-llm mode; --days 400 should not override.
    result = _run(
        "--real-llm",
        "--estimate-only",
        "--days", "400",
        env_overrides={"ANTHROPIC_API_KEY": "placeholder-key"},
    )
    assert result.returncode == 0
    # Cost should be ~$9 (60 * 0.15), not ~$60 (400 * 0.15).
    assert "$9.00" in result.stdout
    assert "$60.00" not in result.stdout
