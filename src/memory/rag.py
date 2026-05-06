"""RAG memory — retrieve historically similar decisions for the current context.

Two backends, picked by availability:

    * Chroma + sentence-transformers (production)
    * In-memory TF-IDF over journal text (fallback)

Both expose the same interface: ``add(doc_id, text, metadata)`` and
``query_similar(context, k)``.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from common.config import get_setting
from common.logging import get_logger

log = get_logger(__name__)


class RAGMemory:
    """Lightweight RAG over the Decision Journal.

    Chroma is loaded lazily so the rest of the stack works without it.
    """

    def __init__(self, store_path: str | Path | None = None) -> None:
        self.store_path = Path(
            store_path or get_setting("memory.rag_store", "data_store/chroma")
        )
        self.store_path.mkdir(parents=True, exist_ok=True)
        self.top_k = int(get_setting("memory.rag_top_k", 5))
        self._collection = None
        self._backend = self._init_chroma()

    # ------------------------------------------------------------------
    def _init_chroma(self) -> str:
        try:
            import chromadb  # type: ignore

            client = chromadb.PersistentClient(path=str(self.store_path))
            self._collection = client.get_or_create_collection(
                name="mais_decisions",
                metadata={"hnsw:space": "cosine"},
            )
            log.debug("rag.chroma_ready", path=str(self.store_path))
            return "chroma"
        except Exception as e:
            log.debug("rag.chroma_unavailable", error=str(e))
            return "tfidf"

    # ------------------------------------------------------------------
    def add(self, doc_id: str, text: str, metadata: dict[str, Any]) -> None:
        if self._backend == "chroma" and self._collection is not None:
            try:
                self._collection.upsert(
                    ids=[doc_id],
                    documents=[text],
                    metadatas=[{k: _coerce(v) for k, v in metadata.items()}],
                )
                return
            except Exception as e:
                log.warning("rag.chroma_add_failed", error=str(e))

    def query_similar(self, context: dict[str, Any], k: int | None = None) -> list[dict]:
        k = k or self.top_k
        if self._backend == "chroma" and self._collection is not None:
            try:
                query = json.dumps(context, ensure_ascii=False, default=str)[:4000]
                res = self._collection.query(query_texts=[query], n_results=k)
                docs = (res.get("documents") or [[]])[0]
                metas = (res.get("metadatas") or [[]])[0]
                return [
                    {"text": d, "metadata": m}
                    for d, m in zip(docs, metas, strict=False)
                ]
            except Exception as e:
                log.warning("rag.chroma_query_failed", error=str(e))
        return self._tfidf_query(context, k)

    # ------------------------------------------------------------------
    def _tfidf_query(self, context: dict[str, Any], k: int) -> list[dict]:
        """Fallback: rank journal rows by simple bag-of-words overlap."""
        try:
            db_path = Path(
                get_setting("memory.journal_db", "data_store/journal.sqlite")
            )
            if not db_path.exists():
                return []
            agent = context.get("agent", "")
            q_text = json.dumps(context, ensure_ascii=False, default=str).lower()
            q_tokens = {t for t in q_text.split() if len(t) > 2}
            if not q_tokens:
                return []
            with sqlite3.connect(db_path) as c:
                cur = c.execute(
                    "SELECT id, agent, action, ticker, rationale, context_json, "
                    "outcome_1w, outcome_1m FROM decisions "
                    "WHERE agent = ? OR ? = '' "
                    "ORDER BY ts DESC LIMIT 500",
                    (agent, agent),
                )
                rows = cur.fetchall()
            scored: list[tuple[float, dict[str, Any]]] = []
            for row in rows:
                doc_text = " ".join(str(x or "") for x in row[3:6]).lower()
                d_tokens = {t for t in doc_text.split() if len(t) > 2}
                overlap = len(q_tokens & d_tokens) / max(len(q_tokens | d_tokens), 1)
                if overlap == 0:
                    continue
                scored.append(
                    (
                        overlap,
                        {
                            "id": row[0],
                            "agent": row[1],
                            "action": row[2],
                            "ticker": row[3],
                            "rationale": row[4],
                            "outcome_1w": row[6],
                            "outcome_1m": row[7],
                        },
                    )
                )
            scored.sort(key=lambda kv: kv[0], reverse=True)
            return [m for _, m in scored[:k]]
        except Exception as e:
            log.debug("rag.tfidf_failed", error=str(e))
            return []


def _coerce(v: Any) -> Any:
    """Chroma metadata values must be primitives."""
    if isinstance(v, (str, int, float, bool)):
        return v
    return json.dumps(v, ensure_ascii=False, default=str)[:1000]
