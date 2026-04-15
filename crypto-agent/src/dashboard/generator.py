"""Static HTML dashboard generator.

Walks `data/runs/<YYYY-MM-DD>/*.json` run artifacts, aggregates them
into operator-friendly summary tables, and renders one self-contained
HTML file under `data/dashboard/index.html`. No server, no JS framework,
no external CSS.

What the dashboard surfaces:

  1. **Headline** — latest equity, last-run date, number of lifetime runs,
     cumulative LLM cost, days with errors, days halted.
  2. **Equity curve** — SVG line chart of end-of-run equity by date
     (uses `run_artifact.equity_usd`).
  3. **Recent runs table** — last 20 runs: date, run_id (short), equity,
     orders placed, errors, macro regime, run duration, cost.
  4. **Agent ELO weights** — latest weights from the most recent run
     for which the artifact carries `elo_weights`.
  5. **Recent lessons** — tail of the lesson store is not in artifacts
     (it's in the in-memory store which doesn't persist yet), but the
     dashboard shows any reflection lessons it can extract from the
     reflection agent output in the artifact if present.
  6. **Errors** — a flat list of the last 10 errors across all runs.

Design notes:
  - HTML is emitted from plain f-strings. No Jinja dep.
  - SVG is hand-rolled, so no matplotlib/Plotly dep.
  - Everything is escape-safe: user-authored text (lessons, rationale)
    is run through `html.escape`.
  - The generator is pure: input is a list of artifact dicts, output is
    an HTML string. The CLI glue at the bottom does disk I/O.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.logging import get_logger

log = get_logger("dashboard")


# ---------------------------------------------------------------------------
# Loading + aggregation
# ---------------------------------------------------------------------------


def load_run_artifacts(runs_root: Path) -> list[dict[str, Any]]:
    """Walk `data/runs/<date>/*.json` and return every artifact, sorted by ts."""
    out: list[dict[str, Any]] = []
    if not runs_root.exists():
        return out
    for date_dir in sorted(runs_root.iterdir()):
        if not date_dir.is_dir():
            continue
        for run_file in sorted(date_dir.glob("*.json")):
            try:
                out.append(json.loads(run_file.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001
                log.warning("dashboard.bad_artifact", path=str(run_file), err=str(exc))
    out.sort(key=lambda a: a.get("started_at", ""))
    return out


@dataclass
class DashboardStats:
    total_runs: int
    completed_runs: int
    halted_runs: int
    runs_with_errors: int
    latest_equity: float
    latest_run_id: str
    latest_run_date: str
    cumulative_llm_cost_usd: float
    equity_curve: list[tuple[str, float]]  # (date, equity)
    latest_elo_weights: dict[str, float]
    recent_errors: list[tuple[str, str]]  # (run_id, error)


def aggregate(artifacts: list[dict[str, Any]]) -> DashboardStats:
    if not artifacts:
        return DashboardStats(
            total_runs=0,
            completed_runs=0,
            halted_runs=0,
            runs_with_errors=0,
            latest_equity=0.0,
            latest_run_id="",
            latest_run_date="",
            cumulative_llm_cost_usd=0.0,
            equity_curve=[],
            latest_elo_weights={},
            recent_errors=[],
        )

    total = len(artifacts)
    halted = sum(1 for a in artifacts if a.get("halted"))
    with_errs = sum(1 for a in artifacts if a.get("errors"))
    completed = total - halted

    cum_cost = 0.0
    for art in artifacts:
        for row in art.get("agent_results", []):
            cum_cost += float(row.get("cost_usd", 0) or 0)

    equity_curve: list[tuple[str, float]] = []
    for art in artifacts:
        if art.get("halted"):
            continue
        date = str(art.get("started_at", ""))[:10]
        eq = float(art.get("equity_usd", 0) or 0)
        if eq > 0 and date:
            equity_curve.append((date, eq))

    latest = artifacts[-1]
    latest_weights = dict(latest.get("elo_weights") or {})

    errors: list[tuple[str, str]] = []
    for art in reversed(artifacts):
        run_id = str(art.get("run_id", ""))
        for err in art.get("errors", [])[:5]:
            errors.append((run_id, str(err)))
            if len(errors) >= 10:
                break
        if len(errors) >= 10:
            break

    return DashboardStats(
        total_runs=total,
        completed_runs=completed,
        halted_runs=halted,
        runs_with_errors=with_errs,
        latest_equity=float(latest.get("equity_usd", 0) or 0),
        latest_run_id=str(latest.get("run_id", "")),
        latest_run_date=str(latest.get("started_at", ""))[:10],
        cumulative_llm_cost_usd=cum_cost,
        equity_curve=equity_curve,
        latest_elo_weights=latest_weights,
        recent_errors=errors,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _equity_svg(points: list[tuple[str, float]], width: int = 760, height: int = 200) -> str:
    """Hand-rolled SVG line chart for the equity curve."""
    if len(points) < 2:
        return (
            f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
            f'<rect width="100%" height="100%" fill="#f7f7f7"/>'
            f'<text x="{width // 2}" y="{height // 2}" text-anchor="middle" '
            f'font-family="monospace" font-size="14" fill="#888">'
            f'need at least 2 runs</text></svg>'
        )

    values = [v for _, v in points]
    lo = min(values)
    hi = max(values)
    span = hi - lo if hi > lo else 1.0
    pad = 30

    def _x(i: int) -> float:
        return pad + (width - 2 * pad) * i / (len(points) - 1)

    def _y(v: float) -> float:
        return height - pad - (height - 2 * pad) * (v - lo) / span

    path_parts = []
    for i, (_, v) in enumerate(points):
        cmd = "M" if i == 0 else "L"
        path_parts.append(f"{cmd}{_x(i):.1f},{_y(v):.1f}")
    path = " ".join(path_parts)

    # Gridlines at lo / mid / hi
    mid = (lo + hi) / 2
    grid = ""
    for label, val in (("hi", hi), ("mid", mid), ("lo", lo)):
        y = _y(val)
        grid += (
            f'<line x1="{pad}" y1="{y:.1f}" x2="{width - pad}" y2="{y:.1f}" '
            f'stroke="#ddd" stroke-width="1"/>'
            f'<text x="{pad - 4}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-family="monospace" font-size="11" fill="#555">'
            f'${val:,.0f}</text>'
        )

    # Start/end date labels
    start_label = _esc(points[0][0])
    end_label = _esc(points[-1][0])
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'style="background:#fafafa;border:1px solid #e0e0e0;">'
        f'{grid}'
        f'<path d="{path}" fill="none" stroke="#1a73e8" stroke-width="2"/>'
        f'<text x="{pad}" y="{height - 8}" font-family="monospace" font-size="11" fill="#666">'
        f'{start_label}</text>'
        f'<text x="{width - pad}" y="{height - 8}" text-anchor="end" '
        f'font-family="monospace" font-size="11" fill="#666">{end_label}</text>'
        f'</svg>'
    )


def _elo_bars(weights: dict[str, float]) -> str:
    if not weights:
        return '<p style="color:#888;font-family:monospace;">no ELO data yet</p>'
    rows = []
    max_w = max(weights.values()) if weights else 1.0
    for agent, w in sorted(weights.items(), key=lambda kv: kv[1], reverse=True):
        pct = 100.0 * w / max_w if max_w > 0 else 0.0
        rows.append(
            f'<tr><td style="width:80px;">{_esc(agent)}</td>'
            f'<td style="width:460px;">'
            f'<div style="background:#1a73e8;height:14px;width:{pct:.1f}%;"></div>'
            f'</td>'
            f'<td style="width:80px;text-align:right;font-family:monospace;">{w:.3f}</td></tr>'
        )
    return (
        '<table style="border-collapse:collapse;">'
        f'{"".join(rows)}'
        '</table>'
    )


def _runs_table(artifacts: list[dict[str, Any]], limit: int = 20) -> str:
    if not artifacts:
        return '<p style="color:#888;font-family:monospace;">no runs yet</p>'
    recent = list(reversed(artifacts[-limit:]))
    rows = []
    for art in recent:
        rid = _esc(str(art.get("run_id", ""))[:8])
        date = _esc(str(art.get("started_at", ""))[:19])
        eq = float(art.get("equity_usd", 0) or 0)
        n_orders = len(art.get("approved_orders", []) or [])
        n_errors = len(art.get("errors", []) or [])
        halted = "⛔" if art.get("halted") else ""
        regime = _esc(art.get("macro_regime", ""))
        dur_ms = int(art.get("duration_ms", 0) or 0)
        cost = sum(float(r.get("cost_usd", 0) or 0) for r in art.get("agent_results", []))
        rows.append(
            f'<tr>'
            f'<td style="font-family:monospace;">{date}</td>'
            f'<td style="font-family:monospace;">{rid}</td>'
            f'<td style="font-family:monospace;text-align:right;">${eq:,.2f}</td>'
            f'<td style="text-align:right;">{n_orders}</td>'
            f'<td style="text-align:right;color:{"#d14" if n_errors else "#888"};">{n_errors}</td>'
            f'<td>{regime}</td>'
            f'<td>{halted}</td>'
            f'<td style="text-align:right;font-family:monospace;">{dur_ms} ms</td>'
            f'<td style="text-align:right;font-family:monospace;">${cost:.4f}</td>'
            f'</tr>'
        )
    return (
        '<table style="border-collapse:collapse;width:100%;">'
        '<thead><tr style="border-bottom:2px solid #333;">'
        '<th style="text-align:left;">started</th><th style="text-align:left;">run_id</th>'
        '<th style="text-align:right;">equity</th><th style="text-align:right;">orders</th>'
        '<th style="text-align:right;">errors</th><th>regime</th>'
        '<th></th><th style="text-align:right;">dur</th>'
        '<th style="text-align:right;">LLM $</th></tr></thead>'
        '<tbody>'
        f'{"".join(rows)}'
        '</tbody></table>'
    )


def _errors_list(errors: list[tuple[str, str]]) -> str:
    if not errors:
        return '<p style="color:#888;font-family:monospace;">no errors 🎉</p>'
    items = [
        f'<li><code>{_esc(rid[:8])}</code> — {_esc(msg)}</li>'
        for rid, msg in errors
    ]
    return f'<ul>{"".join(items)}</ul>'


def render_html(stats: DashboardStats, artifacts: list[dict[str, Any]]) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>crypto-agent dashboard</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
         sans-serif; max-width: 900px; margin: 2em auto; padding: 0 1em;
         color:#222; background:#fff; }}
  h1 {{ margin-bottom: 0.1em; }}
  .subtitle {{ color:#888; font-family: monospace; margin-top:0; }}
  .headline {{ display:flex; gap:2em; flex-wrap:wrap; margin: 1.5em 0; }}
  .card {{ padding: 0.8em 1.2em; border:1px solid #e0e0e0; border-radius:6px;
           background:#fafafa; min-width: 150px; }}
  .card .label {{ font-size: 0.8em; color:#666; text-transform: uppercase;
                  letter-spacing: 0.05em; }}
  .card .value {{ font-size: 1.5em; font-weight:600; font-family:monospace; }}
  h2 {{ border-bottom: 1px solid #e0e0e0; padding-bottom: 0.3em; margin-top: 2em; }}
  table {{ font-size: 0.9em; }}
  th, td {{ padding: 0.25em 0.5em; }}
  tbody tr:nth-child(even) {{ background: #f7f7f7; }}
  code {{ background: #f0f0f0; padding: 0 0.3em; border-radius: 3px; }}
  footer {{ margin-top: 3em; color: #888; font-family: monospace; font-size: 0.85em; }}
</style>
</head>
<body>
<h1>crypto-agent dashboard</h1>
<p class="subtitle">generated {generated_at}</p>

<div class="headline">
  <div class="card"><div class="label">latest equity</div>
       <div class="value">${stats.latest_equity:,.2f}</div></div>
  <div class="card"><div class="label">runs total</div>
       <div class="value">{stats.total_runs}</div></div>
  <div class="card"><div class="label">halted</div>
       <div class="value">{stats.halted_runs}</div></div>
  <div class="card"><div class="label">with errors</div>
       <div class="value">{stats.runs_with_errors}</div></div>
  <div class="card"><div class="label">cumulative LLM $</div>
       <div class="value">${stats.cumulative_llm_cost_usd:.4f}</div></div>
</div>

<h2>Equity curve</h2>
{_equity_svg(stats.equity_curve)}

<h2>ELO agent weights (latest run)</h2>
{_elo_bars(stats.latest_elo_weights)}

<h2>Recent runs</h2>
{_runs_table(artifacts)}

<h2>Recent errors</h2>
{_errors_list(stats.recent_errors)}

<footer>
crypto-agent · latest run <code>{_esc(stats.latest_run_id)}</code> on
{_esc(stats.latest_run_date)} · this page is a static artifact produced by
<code>python -m src.dashboard.generator</code>
</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI glue
# ---------------------------------------------------------------------------


def generate_dashboard(
    runs_root: Path | None = None,
    output: Path | None = None,
) -> Path:
    from src.orchestrator.run_artifact import RUNS_ROOT

    root = runs_root or RUNS_ROOT
    artifacts = load_run_artifacts(root)
    stats = aggregate(artifacts)
    html_text = render_html(stats, artifacts)

    out = output or (Path("data/dashboard/index.html"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    log.info("dashboard.generated", path=str(out), runs=stats.total_runs)
    return out


def main() -> int:  # pragma: no cover — CLI glue
    path = generate_dashboard()
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
