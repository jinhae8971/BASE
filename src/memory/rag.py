"""RAG memory over past decisions (Chroma).

Each decision is embedded (or indexed) and stored so that future agents can
retrieve the top-K most similar past situations to condition their reasoning.

Phase 0: minimal keyword search (no embeddings yet). Phase 4 upgrades to
Chroma + sentence-transformers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from common.config import get_setting
from common.logging import get_logger

log = get_logger(__name__)


class RAGMemory:
    def __init__(self, store_path: str | Path | None = None) -> None:
        self.store_path = Path(
            store_path or get_setting("memory.rag_store", "data_store/chroma")
        )
        self.store_path.mkdir(parents=True, exist_ok=True)
        self.top_k = int(get_setting("memory.rag_top_k", 5))

    def query_similar(self, context: dict[str, Any], k: int | None = None) -> list[dict]:
        """Return up to K historically similar situations.

        Phase 0 returns an empty list; downstream code still works because it
        simply doesn't prepend any examples.
        """
        k = k or self.top_k
        log.debug("rag.query_stub", k=k)
        return []

    def add(self, doc_id: str, text: str, metadata: dict[str, Any]) -> None:
        log.debug("rag.add_stub", id=doc_id)
