"""RAG memory over past decisions — ChromaDB with ONNX embeddings.

Phase 1: persistent ChromaDB store with ONNXMiniLM embeddings.
Each decision is indexed so future agents can retrieve top-K similar
historical situations to condition their reasoning.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from common.config import get_setting
from common.logging import get_logger

log = get_logger(__name__)


def _get_embedding_fn() -> Any:
    try:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2  # type: ignore
        return ONNXMiniLM_L6_V2()
    except Exception:
        try:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction  # type: ignore
            return DefaultEmbeddingFunction()
        except Exception:
            return None


class RAGMemory:
    def __init__(self, store_path: str | Path | None = None) -> None:
        self.store_path = Path(
            store_path or get_setting("memory.rag_store", "data_store/chroma")
        )
        self.store_path.mkdir(parents=True, exist_ok=True)
        self.top_k = int(get_setting("memory.rag_top_k", 5))
        self._col: Any = None

    def _collection(self) -> Any:
        if self._col is not None:
            return self._col
        try:
            import chromadb  # type: ignore
            client = chromadb.PersistentClient(path=str(self.store_path))
            emb_fn = _get_embedding_fn()
            kwargs: dict[str, Any] = {"name": "decisions"}
            if emb_fn is not None:
                kwargs["embedding_function"] = emb_fn
            self._col = client.get_or_create_collection(**kwargs)
        except Exception as exc:
            log.warning("rag.chroma_init_failed", error=str(exc))
            self._col = None
        return self._col

    def query_similar(self, context: dict[str, Any], k: int | None = None) -> list[dict]:
        k = k or self.top_k
        col = self._collection()
        if col is None:
            return []
        query_text = _dict_to_text(context)
        if not query_text:
            return []
        try:
            count = col.count()
            if count == 0:
                return []
            results = col.query(
                query_texts=[query_text],
                n_results=min(k, count),
            )
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            return [{"text": d, **m} for d, m in zip(docs, metas)]
        except Exception as exc:
            log.warning("rag.query_failed", error=str(exc))
            return []

    def add(self, doc_id: str, text: str, metadata: dict[str, Any]) -> None:
        col = self._collection()
        if col is None:
            return
        try:
            clean_meta = {k: str(v) for k, v in metadata.items() if v is not None}
            col.upsert(documents=[text], metadatas=[clean_meta], ids=[doc_id])
        except Exception as exc:
            log.warning("rag.add_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dict_to_text(d: dict[str, Any]) -> str:
    """Flatten a context dict into a search-friendly string."""
    parts: list[str] = []
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool)):
            parts.append(f"{k}:{v}")
        elif isinstance(v, dict):
            parts.append(f"{k}:{json.dumps(v, ensure_ascii=False)[:200]}")
    return " ".join(parts)[:2000]
