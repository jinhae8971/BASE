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
