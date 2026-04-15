"""Reflection agent — analyzes past decisions to improve future ones.

Runs weekly/monthly. For every specialist agent, it computes attribution
(how much P&L their picks contributed), identifies recurring failure modes,
and proposes prompt/parameter updates. Proposals are written to
`data_store/reflections/YYYY-MM-DD.md` for human review.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from common.config import get_env
from common.llm import call_claude
from common.logging import get_logger
from common.types import AgentProposal

from .base import BaseAgent

log = get_logger(__name__)


class ReflectionAgent(BaseAgent):
    name = "reflection"
    prompt_file = "reflection.md"

    def gather_context(self, as_of: date) -> dict[str, Any]:
        from memory.journal import summarize_decisions

        return summarize_decisions(as_of, lookback_days=30)

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
        report = call_claude(
            system=prompt,
            messages=[{"role": "user", "content": str(ctx)}],
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
