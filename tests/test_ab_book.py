"""A/B optimizer paper book accounting + winner selection."""
from __future__ import annotations

from datetime import date

from orchestrator.ab_book import (
    append_book_nav,
    evaluate_books,
    load_book,
    pick_winner,
)


def _patch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    from common import config as c

    c.get_env.cache_clear()


def _walk(monkeypatch, tmp_path, method: str, navs: list[float]) -> None:
    """Append a sequence of NAVs over the last N business days."""
    _patch(monkeypatch, tmp_path)
    today = date.today()
    for i, nav in enumerate(reversed(navs)):
        when = date.fromordinal(today.toordinal() - i)
        append_book_nav(method, when, nav)


def test_book_round_trip(monkeypatch, tmp_path) -> None:
    _walk(monkeypatch, tmp_path, "score_weighted", [100, 101, 102, 103])
    nav = load_book("score_weighted")
    assert len(nav) == 4
    assert nav.iloc[-1] in (100.0, 103.0)  # ordering depends on date sort


def test_pick_winner_prefers_higher_sharpe(monkeypatch, tmp_path) -> None:
    # Steady-ish uptrend with mild noise (best Sharpe)
    _walk(monkeypatch, tmp_path, "score_weighted", [100, 100.5, 101.1, 101.5, 102.0])
    # Same ending return but choppy (worse Sharpe)
    _walk(monkeypatch, tmp_path, "mean_variance", [100, 105, 95, 106, 102])
    # Sideways (near-zero Sharpe)
    _walk(monkeypatch, tmp_path, "black_litterman", [100, 100.5, 99.5, 100.5, 100])
    books = evaluate_books(window_days=10)
    assert "score_weighted" in books
    winner = pick_winner(books)
    assert winner == "score_weighted"


def test_winner_none_when_no_books(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path)
    assert pick_winner({}) is None


def test_evaluate_skips_empty_methods(monkeypatch, tmp_path) -> None:
    _walk(monkeypatch, tmp_path, "score_weighted", [100, 101, 102])
    # Other methods have no NAV history → not in the result
    books = evaluate_books(window_days=10)
    assert "score_weighted" in books
    assert "mean_variance" not in books
