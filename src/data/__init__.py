"""Data ingestion modules.

Each submodule exposes thin `fetch_*` functions that return plain dicts so
they can be embedded into LLM prompts directly. Implementations are stubs
in Phase 0 — Phase 1 will wire them to pykrx / DART / ECOS / news sources.
"""
from .macro import fetch_macro_snapshot
from .market import fetch_factor_panel, fetch_sector_snapshot, fetch_price_series
from .fundamentals import fetch_value_candidates
from .news import fetch_news_headlines

__all__ = [
    "fetch_macro_snapshot",
    "fetch_factor_panel",
    "fetch_sector_snapshot",
    "fetch_price_series",
    "fetch_value_candidates",
    "fetch_news_headlines",
]
