"""Reflection agent — analyzes past decisions to improve future ones.

Runs weekly/monthly. Computes per-agent attribution from journaled outcomes
(1w / 1m forward returns), benchmark-relative performance, identifies
recurring failure modes, and proposes prompt/parameter updates as a Markdown
report under ``$MAIS_DATA_DIR/reflections/<YYYY-MM-DD>.md``.

**A human reviews and applies any proposed changes — never the agent itself.**
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from common.config import get_env
from common.llm import call_claude
from common.logging import get_logger
from common.types import AgentProposal

from .base import BaseAgent

log = get_logger(__name__)


class ReflectionAgent(BaseAgent):
    name = "reflection"
    prompt_file = "reflection.md"
    use_rag = False

    def gather_context(self, as_of: date) -> dict[str, Any]:
        from data.market import fetch_benchmark_series
        from memory.journal import summarize_decisions
        from portfolio.risk import compute_portfolio_metrics
        from portfolio.risk_guards import load_recent_equity

        ctx: dict[str, Any] = summarize_decisions(as_of, lookback_days=30)

        eq = load_recent_equity(lookback_days=60)
        if not eq.empty and len(eq) >= 5:
            rets = eq.pct_change().dropna()
            bench = fetch_benchmark_series(
                start=eq.index.min().date() - timedelta(days=5),
                end=as_of,
            )
            bench_rets = (
                bench.reindex(eq.index, method="pad").pct_change().dropna()
                if not bench.empty
                else pd.Series(dtype=float)
            )
            metrics = compute_portfolio_metrics(rets, benchmark_returns=bench_rets)
            ctx["performance_30d"] = {
                "cagr": metrics.cagr,
                "sharpe": metrics.sharpe,
                "mdd": metrics.mdd,
                "alpha": metrics.alpha,
                "tracking_error": metrics.tracking_error,
                "information_ratio": metrics.information_ratio,
            }
        return ctx

    def parse_response(self, text: str, as_of: date) -> AgentProposal:
        return AgentProposal(
            agent_name=self.name,
            as_of=as_of,
            conviction=0,
            rationale=text,
        )

    def reflect(self, as_of: date) -> Path:
        ctx = self.gather_context(as_of)
        prompt = self._load_system_prompt()
        body = json.dumps(ctx, ensure_ascii=False, default=str, indent=2)
        report = call_claude(
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"기간: 최근 30일 (기준일 {as_of.isoformat()}).\n\n"
                        f"```json\n{body}\n```"
                    ),
                }
            ],
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        env = get_env()
        out_dir = Path(env.mais_data_dir) / "reflections"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{as_of.isoformat()}.md"
        out_path.write_text(report, encoding="utf-8")
        log.info("reflection.written", path=str(out_path))
        return out_path


def main() -> None:
    from datetime import date as _date

    ReflectionAgent().reflect(_date.today())


if __name__ == "__main__":
    main()
