"""Self-learning loop orchestrator.

Given a `ClosedPosition`, runs the Reflection agent, stores its natural-
language lesson in the vector store (so the next daily run's RAG
retrieval surfaces it), and updates the `EloTable` with the per-agent
scoring deltas. The next workflow run's aggregator reads those ELO
weights and automatically tilts toward whichever specialist agents have
been right most recently.

This module is the bridge that closes the user's stated core requirement:
*"지난 투자이력을 통해 스스로 실력을 쌓아가는 구조"*. Everything else in
the system just routes signals; this is the only place where the system
actually *changes its own behavior* as a function of realized outcomes.

The loop is side-effect heavy (vector store insert + ELO mutation), so
failures are caught, logged, and swallowed — a broken reflection must not
take down the daily trading loop.
"""

from __future__ import annotations

from typing import Any

from src.agents.base import AgentContext
from src.agents.reflection import ReflectionAgent
from src.learning.elo import EloTable
from src.learning.position_tracker import ClosedPosition
from src.llm import LLMClient
from src.logging import get_logger
from src.memory.vector_store import InMemoryLessonStore, Lesson, default_store

log = get_logger("learning_loop")


class LearningLoop:
    def __init__(
        self,
        elo: EloTable,
        lesson_store: InMemoryLessonStore | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.elo = elo
        self.lesson_store = lesson_store or default_store()
        self.llm_client = llm_client
        self.reflection = ReflectionAgent(llm_client=llm_client)
        #: Bookkeeping so tests (and Grafana) can see the loop worked.
        self.closures_processed = 0
        self.lessons_written = 0

    async def on_closed(self, closed: ClosedPosition) -> dict[str, Any] | None:
        """Run reflection + persist lesson + update ELO for one closed position.

        Returns the reflection payload on success, or None on failure.
        Never raises.
        """
        ctx = self._build_context(closed)
        try:
            result = await self.reflection.run(ctx)
        except Exception as exc:  # noqa: BLE001
            log.error("learning.reflection_failed", symbol=closed.symbol, err=str(exc))
            return None

        payload = result.payload
        try:
            await self.lesson_store.add(
                Lesson(
                    text=payload["lesson"],
                    tags=payload.get("tags", []),
                    trade_id=f"{closed.entry.run_id}:{closed.symbol}",
                )
            )
            self.lessons_written += 1
        except Exception as exc:  # noqa: BLE001
            log.error("learning.lesson_write_failed", err=str(exc))

        try:
            self.elo.update_from_scores(payload.get("agent_scores", {}))
        except Exception as exc:  # noqa: BLE001
            log.error("learning.elo_update_failed", err=str(exc))

        self.closures_processed += 1
        log.info(
            "learning.closed_processed",
            symbol=closed.symbol,
            pnl_pct=round(closed.realized_pnl_pct, 2),
            holding_days=round(closed.holding_period_days, 2),
            lessons_total=self.lessons_written,
        )
        return payload

    # --- helpers ------------------------------------------------------

    def _build_context(self, closed: ClosedPosition) -> AgentContext:
        return AgentContext(
            run_id=closed.entry.run_id,
            as_of=closed.closed_at,
            universe=closed.entry.universe,
            portfolio={
                "symbol": closed.symbol,
                "side": "CLOSED",
                "entry_price": closed.avg_entry_price,
                "exit_price": closed.exit_price,
                "holding_period_days": closed.holding_period_days,
                "realized_pnl_pct": closed.realized_pnl_pct,
                "realized_pnl_usd": closed.realized_pnl_usd,
                "agent_decisions": closed.entry.agent_payloads,
                "macro_regime_at_entry": closed.entry.macro_regime,
            },
        )
