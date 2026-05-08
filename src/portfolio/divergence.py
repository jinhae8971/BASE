"""Paper-vs-live divergence monitor.

When the user transitions from paper to live we recommend running both
books in parallel for a few weeks. This module compares the two NAV curves
and alerts when they drift apart by more than a threshold — a divergence
typically means a broker bug, missed order, or unexplained reconciliation
gap.

Inputs:
    - ``data_store/nav_history.csv`` (already maintained by EOD; we treat
      it as the *live* curve).
    - ``data_store/paper_nav_history.csv`` (a parallel curve that the
      operator drives manually OR a paper backend produces — same schema).

Output: a small dict the morning report can render plus a Slack alert
when ``|live - paper| / paper > threshold``.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from common.config import get_env, get_setting
from common.logging import get_logger
from common.notifications import notify_warning

log = get_logger(__name__)


def _load_nav(filename: str) -> pd.Series:
    p = Path(get_env().mais_data_dir) / filename
    if not p.exists():
        return pd.Series(dtype=float)
    try:
        df = pd.read_csv(p, parse_dates=["date"], index_col="date")
        return df["nav"].astype(float)
    except Exception as e:
        log.debug("divergence.load_failed", file=filename, error=str(e))
        return pd.Series(dtype=float)


def evaluate_divergence(as_of: date | None = None) -> dict:
    """Compare live vs paper NAV curves on the latest common date.

    Returns ``{
        "live_nav": float,
        "paper_nav": float,
        "diff_pct": float,
        "breached": bool,
        "threshold": float,
        "as_of": "YYYY-MM-DD"
    }``.
    """
    threshold = float(get_setting("monitor.divergence_threshold", 0.005))
    live = _load_nav("nav_history.csv")
    paper = _load_nav("paper_nav_history.csv")
    if live.empty or paper.empty:
        return {
            "live_nav": 0.0,
            "paper_nav": 0.0,
            "diff_pct": 0.0,
            "breached": False,
            "threshold": threshold,
            "as_of": (as_of or date.today()).isoformat(),
            "_skipped": "missing_curve",
        }

    common = live.index.intersection(paper.index)
    if common.empty:
        return {
            "live_nav": 0.0,
            "paper_nav": 0.0,
            "diff_pct": 0.0,
            "breached": False,
            "threshold": threshold,
            "as_of": (as_of or date.today()).isoformat(),
            "_skipped": "no_common_date",
        }

    last = common.max()
    live_nav = float(live.loc[last])
    paper_nav = float(paper.loc[last])
    if paper_nav <= 0:
        return {
            "live_nav": live_nav,
            "paper_nav": paper_nav,
            "diff_pct": 0.0,
            "breached": False,
            "threshold": threshold,
            "as_of": str(last.date() if hasattr(last, "date") else last),
            "_skipped": "zero_paper_nav",
        }

    # Normalise both curves so we compare *return* divergence, not absolute
    # capital differences (paper book may have a different starting NAV).
    live_norm = live.loc[common] / live.loc[common.min()]
    paper_norm = paper.loc[common] / paper.loc[common.min()]
    diff_pct = float(live_norm.iloc[-1] / paper_norm.iloc[-1] - 1)

    breached = abs(diff_pct) > threshold
    if breached:
        notify_warning(
            "Paper-vs-live divergence",
            f"diff {diff_pct:+.2%} | live {live_nav:,.0f} paper {paper_nav:,.0f}",
            threshold=f"{threshold:.2%}",
            as_of=str(last.date() if hasattr(last, "date") else last),
        )
        log.warning(
            "divergence.breached",
            diff_pct=round(diff_pct, 4),
            live=live_nav,
            paper=paper_nav,
        )

    return {
        "live_nav": live_nav,
        "paper_nav": paper_nav,
        "diff_pct": round(diff_pct, 4),
        "breached": breached,
        "threshold": threshold,
        "as_of": str(last.date() if hasattr(last, "date") else last),
    }


def append_paper_nav(when: date, nav: float) -> None:
    """Helper for tests / manual operator: append a row to paper_nav_history.csv."""
    p = Path(get_env().mais_data_dir) / "paper_nav_history.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    header = not p.exists()
    with p.open("a", encoding="utf-8") as f:
        if header:
            f.write("date,nav\n")
        f.write(f"{when.isoformat()},{nav:.2f}\n")
