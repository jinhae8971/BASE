"""Data ingestion modules.

Each submodule exposes thin ``fetch_*`` functions that return plain dicts so
they can be embedded into LLM prompts directly.
"""
from .fundamentals import fetch_value_candidates
from .macro import fetch_macro_snapshot
from .market import (
    fetch_benchmark_series,
    fetch_close_panel,
    fetch_factor_panel,
    fetch_latest_prices,
    fetch_price_series,
    fetch_sector_snapshot,
)
from .news import fetch_news_headlines
from .universe import get_universe, sector_map

__all__ = [
    "fetch_benchmark_series",
    "fetch_close_panel",
    "fetch_factor_panel",
    "fetch_latest_prices",
    "fetch_macro_snapshot",
    "fetch_news_headlines",
    "fetch_price_series",
    "fetch_sector_snapshot",
    "fetch_value_candidates",
    "get_universe",
    "sector_map",
]
