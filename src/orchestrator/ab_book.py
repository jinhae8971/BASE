"""A/B optimizer paper books.

Run the same consensus through every available optimizer in parallel,
each persisting its own NAV history. Once a month we compare the books
and either:

    * Notify which optimizer is winning, OR
    * (Optional) auto-rotate the production optimizer to last month's
      best Sharpe, *bounded* by an exhibition-only flag.

Auto-rotation is *off by default* — operator must enable
``optimizer.ab_auto_rotate: true`` in settings.yaml.

State is kept in ``$MAIS_DATA_DIR/ab_books/<method>_nav.csv`` so a long
window of attribution is available for analysis.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from common.config import get_env, get_setting
from common.logging import get_logger
from common.notifications import notify_info
from common.types import PortfolioTarget

from .optimizer import PortfolioOptimizer

log = get_logger(__name__)

METHODS: tuple[str, ...] = ("score_weighted", "mean_variance", "black_litterman")


def _book_path(method: str) -> Path:
    return Path(get_env().mais_data_dir) / "ab_books" / f"{method}_nav.csv"


def append_book_nav(method: str, when: date, nav: float) -> None:
    p = _book_path(method)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = not p.exists()
    with p.open("a", encoding="utf-8") as f:
        if header:
            f.write("date,nav\n")
        f.write(f"{when.isoformat()},{nav:.2f}\n")


def load_book(method: str) -> pd.Series:
    p = _book_path(method)
    if not p.exists():
        return pd.Series(dtype=float)
    try:
        df = pd.read_csv(p, parse_dates=["date"], index_col="date")
        return df["nav"].astype(float).sort_index()
    except Exception as e:
        log.debug("ab_book.load_failed", method=method, error=str(e))
        return pd.Series(dtype=float)


def shadow_optimize_all(
    consensus: dict[str, Any],
    *,
    sector_map: dict[str, str] | None = None,
) -> dict[str, PortfolioTarget]:
    """Run every method on the same consensus.

    Returns ``{method: PortfolioTarget}``. Methods that fail (e.g. cvxpy
    not installed) are silently skipped.
    """
    out: dict[str, PortfolioTarget] = {}
    for method in METHODS:
        try:
            opt = PortfolioOptimizer()
            opt.method = method
            out[method] = opt.optimize(consensus, sector_map=sector_map)
        except Exception as e:
            log.debug("ab_book.method_failed", method=method, error=str(e))
    return out


def _sharpe(returns: pd.Series, rf: float = 0.03) -> float:
    if returns.empty or returns.std() == 0:
        return 0.0
    import numpy as _np

    excess = returns.mean() * 252 - rf
    vol = returns.std() * _np.sqrt(252)
    return float(excess / vol) if vol > 0 else 0.0


def evaluate_books(window_days: int = 30) -> dict[str, dict[str, float]]:
    """Sharpe + total return on each book over the last ``window_days``."""
    out: dict[str, dict[str, float]] = {}
    cutoff_lo = pd.Timestamp.today() - pd.Timedelta(days=window_days * 2)
    for method in METHODS:
        nav = load_book(method)
        if nav.empty:
            continue
        nav = nav[nav.index >= cutoff_lo].tail(window_days + 1)
        if len(nav) < 2:
            continue
        ret = nav.pct_change().dropna()
        out[method] = {
            "sharpe": _sharpe(ret),
            "total_return": float(nav.iloc[-1] / nav.iloc[0] - 1),
            "n_obs": len(ret),
        }
    return out


def pick_winner(
    books: dict[str, dict[str, float]] | None = None,
) -> str | None:
    books = books if books is not None else evaluate_books()
    if not books:
        return None
    return max(books.items(), key=lambda kv: kv[1]["sharpe"])[0]


def rotate_if_enabled(as_of: date | None = None) -> dict[str, Any]:
    """Monthly close-out: pick the winner and (optionally) rotate.

    Returns a summary dict. Always notifies — operators benefit from seeing
    the standing even if auto-rotation is off.
    """
    as_of = as_of or date.today()
    # Only run this on the last day of the month
    next_day = as_of + timedelta(days=1)
    if next_day.month == as_of.month:
        return {"_skipped": "not_month_end", "as_of": as_of.isoformat()}

    books = evaluate_books(window_days=21)
    if not books:
        return {"_skipped": "no_books", "as_of": as_of.isoformat()}

    winner = pick_winner(books)
    current = str(get_setting("optimizer.method", "score_weighted"))
    auto_rotate = bool(get_setting("optimizer.ab_auto_rotate", False))

    summary = {
        "as_of": as_of.isoformat(),
        "books": books,
        "winner": winner,
        "current": current,
        "rotated": False,
    }

    if winner and winner != current and auto_rotate:
        # Update settings.yaml in place
        from pathlib import Path as _P  # noqa: N814

        import yaml

        path = _P(__file__).resolve().parents[2] / "config" / "settings.yaml"
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        data.setdefault("optimizer", {})["method"] = winner
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        try:
            from common import config as c

            c.load_yaml_settings.cache_clear()
        except Exception:
            pass
        summary["rotated"] = True

    notify_info(
        "A/B book month-end",
        f"winner={winner or 'n/a'} current={current} rotated={summary['rotated']}",
        **{k: f"{v['sharpe']:.2f}" for k, v in books.items()},
    )
    log.info(
        "ab_book.month_end",
        winner=winner,
        current=current,
        rotated=summary["rotated"],
    )
    return summary
