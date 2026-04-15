"""Post-trade reflection wiring.

On each closed position, build a `ReflectionAgent` context from the stored
trade record plus the agent decisions made at entry, run the reflection, then:
  1. store the `lesson` in the vector store for RAG in next runs,
  2. feed `agent_scores` into the ELO updater.
"""

from __future__ import annotations

from src.agents.reflection import ReflectionAgent
from src.learning.elo import EloTable
from src.memory.trade_store import TradeRecord
from src.memory.vector_store import InMemoryLessonStore, Lesson


async def reflect_on_trade(
    trade: TradeRecord,
    lesson_store: InMemoryLessonStore,
    elo: EloTable,
) -> None:
    agent = ReflectionAgent()
    from src.agents.base import AgentContext

    ctx = AgentContext(
        run_id=trade.run_id,
        as_of=trade.ts,
        universe=[trade.symbol],
        portfolio=trade.portfolio_snapshot,
    )
    result = await agent.run(ctx)
    payload = result.payload

    await lesson_store.add(
        Lesson(text=payload["lesson"], tags=payload.get("tags", []), trade_id=trade.run_id)
    )

    # Convert per-agent scores to a pseudo-Sharpe for ELO.
    elo.update_from_scores(payload["agent_scores"])
