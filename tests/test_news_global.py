"""Global English news fetcher must degrade gracefully when feedparser missing."""
from __future__ import annotations

from datetime import date


def test_returns_stub_when_feedparser_missing(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fail_import(name: str, *a: object, **kw: object):
        if name == "feedparser":
            raise ImportError("forced for test")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fail_import)

    from data.news_global import fetch_global_macro_headlines

    out = fetch_global_macro_headlines(date(2025, 5, 6))
    assert out["_stub"] is True
    assert out["headlines"] == []


def test_default_feed_list_is_non_empty() -> None:
    from data.news_global import GLOBAL_FEEDS

    assert len(GLOBAL_FEEDS) >= 4
    # Every feed must be a (source, url) tuple
    assert all(len(f) == 2 and f[1].startswith("http") for f in GLOBAL_FEEDS)


def test_returns_proper_keys(monkeypatch) -> None:
    """Even when no feeds reach back (offline), top-level keys are stable."""
    # Force feedparser to return zero entries by stubbing its module
    import sys
    import types

    fake = types.SimpleNamespace(parse=lambda url: types.SimpleNamespace(entries=[]))
    monkeypatch.setitem(sys.modules, "feedparser", fake)
    from data.news_global import fetch_global_macro_headlines

    out = fetch_global_macro_headlines(date(2025, 5, 6))
    assert "as_of" in out
    assert "headlines" in out
    assert "n" in out
    assert out["headlines"] == []
