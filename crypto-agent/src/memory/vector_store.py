"""Lesson store backed by Qdrant. Stub for Phase 0."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Lesson:
    text: str
    tags: list[str]
    trade_id: str | None = None


class InMemoryLessonStore:
    def __init__(self) -> None:
        self._lessons: list[Lesson] = []

    async def add(self, lesson: Lesson) -> None:
        self._lessons.append(lesson)

    async def search(self, query: str, k: int = 5) -> list[Lesson]:
        # Phase 0: naive substring match. Phase 2: Qdrant + embeddings.
        q = query.lower()
        return [le for le in self._lessons if q in le.text.lower()][:k]

    async def recent(self, k: int = 5) -> list[Lesson]:
        return list(self._lessons[-k:])


_default_store = InMemoryLessonStore()


def default_store() -> InMemoryLessonStore:
    """Process-wide lesson store.

    Phase 6 will replace this with a persistent Qdrant-backed store; the
    API stays the same (add / search / recent) so call sites don't change.
    """
    return _default_store
