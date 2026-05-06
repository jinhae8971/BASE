"""Daily 1-pager morning report.

Sent to Slack/Telegram at scheduler.morning_report_time (default 08:30 KST,
i.e. 35 min before the open). Summarises:

    - Yesterday's NAV change and rolling MDD
    - Today's planned target (positions + cash)
    - Active risk-guard state
    - Stop signals about to fire today
    - Recent (last 7d) reflection headline if any
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from common.config import get_env
from common.logging import get_logger, setup_logging
from common.notifications import notify
from data.market import fetch_latest_prices
from portfolio.position_state import all_states
from portfolio.risk_guards import load_recent_equity
from portfolio.stops import evaluate_stops

from . import state as state_store

log = get_logger(__name__)


def _yesterday_nav_change() -> tuple[float, float, float]:
    eq = load_recent_equity(lookback_days=30)
    if eq.empty:
        return 0.0, 0.0, 0.0
    nav_today = float(eq.iloc[-1])
    nav_prev = float(eq.iloc[-2]) if len(eq) >= 2 else nav_today
    chg_pct = (nav_today / nav_prev - 1) if nav_prev else 0.0
    rolling_mdd = float(((eq - eq.cummax()) / eq.cummax()).min())
    return nav_today, chg_pct, rolling_mdd


def _imminent_stops(as_of: date) -> list[dict[str, Any]]:
    states = all_states()
    if not states:
        return []
    tickers = [s.ticker for s in states]
    prices = fetch_latest_prices(tickers, as_of=as_of)
    signals = evaluate_stops({s.ticker: s.qty for s in states}, prices)
    return [
        {"ticker": s.ticker, "reason": s.reason, "pnl": f"{s.pnl_pct:+.2%}"}
        for s in signals
    ]


def _latest_reflection_headline() -> str:
    refl_dir = Path(get_env().mais_data_dir) / "reflections"
    if not refl_dir.exists():
        return ""
    files = sorted(refl_dir.glob("*.md"), reverse=True)
    if not files:
        return ""
    cutoff = date.today() - timedelta(days=7)
    try:
        when = date.fromisoformat(files[0].stem)
    except ValueError:
        return ""
    if when < cutoff:
        return ""
    text = files[0].read_text(encoding="utf-8")
    # Take first line / first H1
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:200]
    return ""


def build_morning_report(as_of: date | None = None) -> dict[str, Any]:
    setup_logging()
    as_of = as_of or date.today()

    nav, chg, mdd = _yesterday_nav_change()
    loaded = state_store.load_target(as_of)
    target_summary = ""
    n_pos = 0
    cash_pct = 0.0
    if loaded:
        target, _ = loaded
        n_pos = len(target.positions)
        cash_pct = target.cash_weight
        top = sorted(target.positions.items(), key=lambda kv: kv[1], reverse=True)[:5]
        target_summary = ", ".join(f"{t} {w:.1%}" for t, w in top)

    stops = _imminent_stops(as_of)
    refl = _latest_reflection_headline()

    body_lines = [
        f"NAV {nav:,.0f} ({chg:+.2%})  rolling MDD {mdd:.2%}",
        f"Today target: {n_pos} positions, cash {cash_pct:.0%}",
    ]
    if target_summary:
        body_lines.append(f"Top weights: {target_summary}")
    if stops:
        body_lines.append(f"Imminent stops: {len(stops)} ({', '.join(s['ticker'] for s in stops)})")
    if refl:
        body_lines.append(f"Reflection: {refl}")

    message = "\n".join(body_lines)
    notify(
        title=f"MAIS morning report — {as_of.isoformat()}",
        message=message,
        level="info",
        fields={
            "NAV": f"{nav:,.0f}",
            "Δ%": f"{chg:+.2%}",
            "MDD": f"{mdd:.2%}",
            "positions": n_pos,
            "cash": f"{cash_pct:.0%}",
            "stops": len(stops),
        },
    )
    log.info("morning_report.sent", positions=n_pos, cash=cash_pct, stops=len(stops))
    return {
        "as_of": as_of.isoformat(),
        "nav": nav,
        "chg_pct": chg,
        "mdd": mdd,
        "n_positions": n_pos,
        "cash_pct": cash_pct,
        "stops": stops,
        "reflection_headline": refl,
    }


def heartbeat() -> None:
    setup_logging()
    notify(
        title="MAIS heartbeat",
        message=f"Scheduler alive at {date.today().isoformat()}",
        level="info",
    )


def main() -> None:
    build_morning_report()


if __name__ == "__main__":
    main()
