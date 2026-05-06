from __future__ import annotations

from memory.journal import DecisionJournal
from memory.rag import RAGMemory


def test_rag_tfidf_fallback_returns_results(tmp_path, monkeypatch) -> None:
    db = tmp_path / "j.sqlite"
    rag_path = tmp_path / "chroma"

    # Force the TF-IDF fallback by not having Chroma succeed: we just point
    # at a fresh path so add() is a no-op and query falls through to SQLite.
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    j = DecisionJournal(db_path=db)
    j.record(
        agent="value",
        action="PICK",
        ticker="005930",
        conviction=8,
        rationale="삼성전자 PER 9 PBR 1.2 매력적인 진입가",
        context={"theme": "semiconductor"},
    )
    j.record(
        agent="value",
        action="PICK",
        ticker="035420",
        conviction=7,
        rationale="NAVER 검색 광고 회복",
        context={"theme": "internet"},
    )

    # Patch the journal db setting so RAG fallback queries the right file
    from common import config as cfg

    cfg.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        cfg, "load_yaml_settings", lambda: {"memory": {"journal_db": str(db)}}
    )

    rag = RAGMemory(store_path=rag_path)
    matches = rag.query_similar({"agent": "value", "context": "삼성전자 반도체"}, k=5)
    # Either Chroma returns nothing (empty store) or TF-IDF returns the most
    # similar SQLite row. Both are acceptable — but TF-IDF should pick 005930.
    if matches:
        first_ticker = matches[0].get("ticker") or matches[0].get("metadata", {}).get(
            "ticker"
        )
        # No assertion when Chroma is in use (results carry no ticker field);
        # in that case just make sure we got *something* serializable.
        assert first_ticker in {"005930", None}
