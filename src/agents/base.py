"""Base class for all LLM-backed specialist agents."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Any

from common.config import get_setting
from common.llm import call_claude
from common.logging import get_logger
from common.types import AgentProposal

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "config" / "prompts"
log = get_logger(__name__)


class BaseAgent(ABC):
    """Abstract LLM agent.

    Subclasses must define:
        - ``name``         — stable identifier used in consensus/journal
        - ``prompt_file``  — file name in ``config/prompts``
        - ``gather_context(as_of)`` — research data dict for the prompt
        - ``parse_response(text, as_of)`` — turn raw model output into AgentProposal
    """

    name: str = "base"
    prompt_file: str = "base.md"
    use_rag: bool = True

    def __init__(self) -> None:
        agent_cfg = get_setting(f"agents.{self.name}", {}) or {}
        self.model: str = agent_cfg.get("model", "claude-opus-4-6")
        self.temperature: float = agent_cfg.get("temperature", 0.2)
        self.max_tokens: int = agent_cfg.get("max_tokens", 4000)

    # ------------------------------------------------------------------
    # Pipeline entry point
    # ------------------------------------------------------------------
    def run(self, as_of: date) -> AgentProposal:
        log.info("agent.run.start", agent=self.name, as_of=as_of.isoformat())
        ctx = self.gather_context(as_of)
        if self.use_rag:
            ctx["similar_past_decisions"] = self._rag_examples(ctx)
        system = self._load_system_prompt()
        user_msg = self._format_user_message(ctx, as_of)

        raw = call_claude(
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        proposal = self.parse_response(raw, as_of)
        proposal.context_used = {"keys": sorted(ctx.keys())}
        log.info(
            "agent.run.done",
            agent=self.name,
            conviction=proposal.conviction,
            picks=len(proposal.picks),
        )
        return proposal

    # ------------------------------------------------------------------
    # Overridable hooks
    # ------------------------------------------------------------------
    @abstractmethod
    def gather_context(self, as_of: date) -> dict[str, Any]:
        """Collect research data (prices, financials, news, ...) for the prompt."""

    @abstractmethod
    def parse_response(self, text: str, as_of: date) -> AgentProposal:
        """Convert model JSON output into an AgentProposal."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _load_system_prompt(self) -> str:
        path = PROMPTS_DIR / self.prompt_file
        if not path.exists():
            log.warning("prompt.missing", path=str(path))
            return f"You are the {self.name} specialist. Respond with valid JSON."
        return path.read_text(encoding="utf-8")

    def _format_user_message(self, ctx: dict[str, Any], as_of: date) -> str:
        return (
            f"오늘 날짜(KST): {as_of.isoformat()}\n\n"
            f"아래는 오늘의 리서치 컨텍스트입니다. 이를 근거로 시스템 프롬프트에서 요구한 "
            f"JSON 스키마로만 답변하세요.\n\n"
            f"```json\n{json.dumps(ctx, ensure_ascii=False, default=str, indent=2)}\n```"
        )

    def _rag_examples(self, ctx: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            from memory.rag import RAGMemory

            rag = RAGMemory()
            return rag.query_similar({"agent": self.name, "context": ctx})
        except Exception as e:
            log.debug("agent.rag_skip", agent=self.name, error=str(e))
            return []

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Best-effort JSON extraction from LLM text."""
        text = text.strip()
        if text.startswith("```"):
            first_nl = text.find("\n")
            text = text[first_nl + 1 :]
            if text.endswith("```"):
                text = text[:-3]
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"No JSON object found in response: {text[:200]}")
        return json.loads(text[start : end + 1])
