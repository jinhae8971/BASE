"""Operator-friendly CLI: ``mais <command>``.

A single entrypoint covering the day-to-day operator tasks:

    mais status      — NAV, regime, positions count, next cron, guards
    mais positions   — per-name PnL, peak, trailing % from peak, pyramid level
    mais today       — today's research target + orders + alerts in one page
    mais doctor      — environment health check (env vars, pykrx, feeds, KIS)
    mais logs        — tail + grep + agent filter on data_store/logs/mais.log
    mais backup      — tar.gz the data_store/ tree
    mais restore     — restore from a backup tarball
    mais set         — write a key into settings.yaml safely
    mais flag        — toggle a feature flag (hedge.enabled, websocket.enabled, ...)
    mais run         — shortcut to run a phase manually
                        (research / order / eod / intraday-stops / monitor / reflect)

Everything uses Rich tables when stdout is a TTY, falls back to plain text
otherwise.
"""
from __future__ import annotations

import json
import re
import sys
import tarfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from common.config import get_env, get_setting

app = typer.Typer(
    add_completion=False,
    help="MAI-System operator CLI — single entrypoint for all daily tasks.",
)
console = Console()


# =====================================================================
# Helpers
# =====================================================================
def _settings_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "settings.yaml"


def _load_settings() -> dict[str, Any]:
    p = _settings_path()
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _save_settings(data: dict[str, Any]) -> None:
    p = _settings_path()
    p.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    # Bust the in-process cache so a long-running scheduler picks it up.
    try:
        from common import config as c

        c.load_yaml_settings.cache_clear()
    except Exception:
        pass


def _set_dotted(d: dict, dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def _get_dotted(d: dict, dotted: str) -> Any:
    cur: Any = d
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _coerce(s: str) -> Any:
    """Best-effort string → Python value (numbers / bool / json)."""
    low = s.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    if (s.startswith("[") and s.endswith("]")) or (
        s.startswith("{") and s.endswith("}")
    ):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    return s


# =====================================================================
# Commands
# =====================================================================
@app.command()
def status() -> None:
    """One-page system snapshot: NAV, regime, positions, next cron, guards."""
    from portfolio.position_state import all_states
    from portfolio.risk_guards import load_recent_equity

    eq = load_recent_equity(lookback_days=30)
    nav = float(eq.iloc[-1]) if not eq.empty else 0.0
    chg = (float(eq.iloc[-1]) / float(eq.iloc[-2]) - 1) if len(eq) >= 2 else 0.0
    mdd = (
        float(((eq - eq.cummax()) / eq.cummax()).min()) if len(eq) >= 2 else 0.0
    )
    states = all_states()

    env = get_env()
    table = Table(title="MAI-System status", show_header=False, expand=False)
    table.add_column("key", style="bold cyan")
    table.add_column("value")
    table.add_row("KIS env", env.kis_env.upper())
    table.add_row("NAV (KRW)", f"{nav:,.0f}")
    table.add_row(
        "yesterday Δ", f"[{'green' if chg >= 0 else 'red'}]{chg:+.2%}[/]"
    )
    table.add_row("rolling MDD", f"{mdd:.2%}")
    table.add_row("positions", str(len(states)))
    if states:
        avg_pyramid = sum(s.pyramid_levels for s in states) / len(states)
        table.add_row("avg pyramid level", f"{avg_pyramid:.1f}")

    table.add_row("research", str(get_setting("scheduler.research_time", "?")))
    table.add_row("order   ", str(get_setting("scheduler.order_time", "?")))
    table.add_row("eod     ", str(get_setting("scheduler.eod_review_time", "?")))

    flags = {
        "hedge.enabled": get_setting("hedge.enabled", False),
        "websocket.enabled": get_setting("websocket.enabled", False),
        "execution.twap_enabled": get_setting("execution.twap_enabled", False),
        "execution.twap_bandit_enabled": get_setting(
            "execution.twap_bandit_enabled", False
        ),
        "optimizer.ab_auto_rotate": get_setting("optimizer.ab_auto_rotate", False),
    }
    for k, v in flags.items():
        sty = "green" if v else "dim"
        table.add_row(k, f"[{sty}]{'ON' if v else 'off'}[/]")

    console.print(table)


@app.command()
def positions() -> None:
    """Per-name positions with PnL, peak, trailing drawdown, pyramid level."""
    from data.market import fetch_latest_prices
    from portfolio.position_state import all_states

    states = all_states()
    if not states:
        console.print("[yellow]No tracked positions.[/]")
        return

    prices = fetch_latest_prices([s.ticker for s in states])
    table = Table(title="Positions")
    for col in ["ticker", "qty", "entry", "peak", "now", "pnl%", "trail%", "pyr"]:
        table.add_column(col, justify="right")
    for s in sorted(states, key=lambda x: x.ticker):
        px = prices.get(s.ticker, 0.0)
        pnl = s.total_pnl_pct(px) if px else 0.0
        trail = s.trailing_drop_pct(px) if px else 0.0
        sty = "green" if pnl >= 0 else "red"
        table.add_row(
            s.ticker,
            f"{s.qty:,}",
            f"{s.entry_price:,.0f}",
            f"{s.peak_price:,.0f}",
            f"{px:,.0f}",
            f"[{sty}]{pnl:+.2%}[/]",
            f"{trail:+.2%}",
            str(s.pyramid_levels),
        )
    console.print(table)


@app.command()
def today() -> None:
    """Today's research target + orders + alerts in one page."""
    from scheduler.state import load_target

    today_d = date.today()
    loaded = load_target(today_d)
    if loaded is None:
        console.print(
            f"[yellow]No saved target for {today_d.isoformat()}.[/] "
            "Run [bold]mais run research[/]."
        )
        return
    target, extra = loaded
    table = Table(title=f"Today's target — {today_d.isoformat()}")
    table.add_column("ticker"), table.add_column("weight", justify="right")
    for tk, w in sorted(
        target.positions.items(), key=lambda kv: kv[1], reverse=True
    )[:25]:
        table.add_row(tk, f"{w:.2%}")
    table.add_row("(cash)", f"{target.cash_weight:.2%}", style="dim")
    console.print(table)
    if extra:
        console.print(f"[dim]extra:[/] {json.dumps(extra, ensure_ascii=False)}")


@app.command()
def doctor() -> None:
    """Environment health check — runs all preflights, reports per-line."""
    table = Table(title="MAI-System doctor")
    table.add_column("check", style="bold")
    table.add_column("status")
    table.add_column("notes")

    checks: list[tuple[str, bool, str]] = []

    env = get_env()
    checks.append(
        (
            "ANTHROPIC_API_KEY",
            bool(env.anthropic_api_key),
            "set" if env.anthropic_api_key else "missing — LLM agents will fail",
        )
    )
    checks.append(
        (
            "KIS_APP_KEY/SECRET",
            bool(env.kis_app_key and env.kis_app_secret),
            f"env={env.kis_env}" if env.kis_app_key else "missing — broker disabled",
        )
    )
    checks.append(
        (
            "KIS_ACCOUNT_NO",
            bool(env.kis_account_no),
            env.kis_account_no or "missing — orders will fail",
        )
    )

    for pkg in ("pykrx", "feedparser", "yfinance", "FinanceDataReader"):
        try:
            __import__(pkg.lower() if pkg == "FinanceDataReader" else pkg)
            checks.append((pkg, True, "installed"))
        except ImportError:
            checks.append((pkg, False, "missing — pip install " + pkg))

    # Calendar / market open?
    try:
        from common.calendar import is_trading_day

        td = is_trading_day(date.today())
        checks.append(
            (
                "today is trading day",
                td,
                "yes" if td else "no — cron will skip phases today",
            )
        )
    except Exception as e:
        checks.append(("calendar", False, str(e)))

    # Settings YAML readable
    try:
        s = _load_settings()
        checks.append(("settings.yaml", True, f"{len(s)} top-level keys"))
    except Exception as e:
        checks.append(("settings.yaml", False, str(e)))

    for name, ok, notes in checks:
        sty = "green" if ok else "red"
        table.add_row(name, f"[{sty}]{'OK' if ok else 'FAIL'}[/]", notes)
    console.print(table)
    n_fail = sum(1 for _, ok, _ in checks if not ok)
    if n_fail:
        console.print(f"\n[red]{n_fail} failures.[/] Fix the missing items above.")
        raise typer.Exit(code=1)
    console.print("\n[green]All checks passed.[/]")


@app.command()
def logs(
    tail: int = typer.Option(100, help="Show last N lines"),
    grep: str = typer.Option("", help="Regex filter"),
    agent: str = typer.Option("", help="Filter to a specific agent name"),
) -> None:
    """Tail / grep / filter the rotating log file."""
    log_path = Path(get_env().mais_data_dir) / "logs" / "mais.log"
    if not log_path.exists():
        console.print(f"[yellow]No log file at {log_path}.[/]")
        return
    pattern = re.compile(grep) if grep else None
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[str] = []
    for ln in lines[-(tail * 3) :]:
        if pattern and not pattern.search(ln):
            continue
        if agent and f"agent={agent}" not in ln and f"'{agent}'" not in ln:
            continue
        out.append(ln)
    for ln in out[-tail:]:
        console.print(ln)


@app.command()
def backup(
    out: Path = typer.Option(
        None, help="Output path. Default: data_store/backups/<ts>.tar.gz"
    ),
) -> None:
    """Tar+gzip the entire data_store/ tree."""
    src = Path(get_env().mais_data_dir)
    if not src.exists():
        console.print(f"[red]No data_store at {src}.[/]")
        raise typer.Exit(code=1)
    if out is None:
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        out = src / "backups" / f"{ts}.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        tar.add(src, arcname=src.name, filter=lambda ti: None if "/backups/" in ti.name else ti)
    console.print(f"[green]Backup written:[/] {out}")


@app.command()
def restore(
    archive: Path = typer.Argument(..., help="Path to a tar.gz produced by ``mais backup``"),
    confirm: str = typer.Option("", help="Must be 'YES' to overwrite data_store/"),
) -> None:
    """Restore data_store/ from a backup. Overwrites — confirmation required."""
    if not archive.exists():
        console.print(f"[red]No such archive:[/] {archive}")
        raise typer.Exit(code=1)
    if confirm != "YES":
        console.print(
            "[red]Refusing to restore.[/] Pass --confirm YES to proceed "
            "(this overwrites data_store/)."
        )
        raise typer.Exit(code=2)
    dest = Path(get_env().mais_data_dir).parent
    with tarfile.open(archive, "r:gz") as tar:
        # Python 3.12+: filter='data' to silence deprecation; older just extracts.
        try:
            tar.extractall(dest, filter="data")  # type: ignore[arg-type]
        except TypeError:
            tar.extractall(dest)
    console.print(f"[green]Restored from[/] {archive} → {dest}")


@app.command(name="set")
def set_(
    key: str = typer.Argument(..., help="Dotted key, e.g. 'risk.hard_stop_pct'"),
    value: str = typer.Argument(..., help="Value (auto-coerced to int/float/bool/json)"),
) -> None:
    """Write a single key into settings.yaml with type coercion."""
    settings = _load_settings()
    old = _get_dotted(settings, key)
    new_v = _coerce(value)
    _set_dotted(settings, key, new_v)
    _save_settings(settings)
    console.print(
        f"[green]set[/] {key}: [dim]{old}[/] → [bold]{new_v}[/]"
    )


@app.command()
def flag(
    name: str = typer.Argument(..., help="Flag, e.g. 'hedge.enabled' or short 'hedge'"),
    on: bool = typer.Argument(..., help="true/false"),
) -> None:
    """Toggle a known feature flag. Short names are auto-expanded."""
    aliases = {
        "hedge": "hedge.enabled",
        "websocket": "websocket.enabled",
        "ws": "websocket.enabled",
        "twap": "execution.twap_enabled",
        "twap_bandit": "execution.twap_bandit_enabled",
        "ab_rotate": "optimizer.ab_auto_rotate",
        "auto_apply": "learning.auto_apply",
        "intraday_stops": "scheduler.intraday_stops_enabled",
    }
    full = aliases.get(name, name)
    settings = _load_settings()
    old = _get_dotted(settings, full)
    _set_dotted(settings, full, bool(on))
    _save_settings(settings)
    console.print(
        f"[green]flag[/] {full}: [dim]{old}[/] → [bold]{'ON' if on else 'off'}[/]"
    )


@app.command()
def init() -> None:
    """Interactive setup wizard — generates .env, validates, runs doctor."""
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    example = root / ".env.example"

    if env_path.exists():
        console.print(f"[yellow].env already exists at[/] {env_path}")
        if not typer.confirm("Overwrite?", default=False):
            console.print("Aborted.")
            return

    console.print("\n[bold cyan]MAI-System setup wizard[/]\n")
    console.print("Press Enter to keep default. Ctrl-C to abort.\n")

    template = example.read_text(encoding="utf-8") if example.exists() else ""
    defaults: dict[str, str] = {}
    for line in template.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            defaults[k.strip()] = v.strip()

    answers: dict[str, str] = {}
    for key, label in [
        ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        ("KIS_APP_KEY", "KIS_APP_KEY"),
        ("KIS_APP_SECRET", "KIS_APP_SECRET"),
        ("KIS_ACCOUNT_NO", "KIS_ACCOUNT_NO (xxxxxxxx-xx)"),
    ]:
        answers[key] = typer.prompt(label, default=defaults.get(key, ""), show_default=False)
    kis_env = typer.prompt("KIS_ENV (paper | live)", default="paper")
    if kis_env.lower() not in ("paper", "live"):
        console.print("[red]KIS_ENV must be 'paper' or 'live'.[/]")
        raise typer.Exit(code=1)
    answers["KIS_ENV"] = kis_env.lower()
    answers["DART_API_KEY"] = typer.prompt(
        "DART_API_KEY (optional)", default="", show_default=False
    )
    answers["SLACK_WEBHOOK_URL"] = typer.prompt(
        "SLACK_WEBHOOK_URL (optional)", default="", show_default=False
    )

    lines: list[str] = ["# Generated by `mais init`. Edit freely.\n"]
    for k, v in answers.items():
        lines.append(f"{k}={v}")
    for stay in (
        "CLAUDE_REASONING_MODEL",
        "CLAUDE_FAST_MODEL",
        "MAIS_DATA_DIR",
        "MAIS_LOG_LEVEL",
        "MAIS_TZ",
    ):
        if stay in defaults and stay not in answers:
            lines.append(f"{stay}={defaults[stay]}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"\n[green]Wrote[/] {env_path}\n")

    try:
        from common import config as c

        c.get_env.cache_clear()
    except Exception:
        pass

    console.print("[bold]Running doctor…[/]\n")
    try:
        doctor()
    except typer.Exit:
        console.print(
            "\n[yellow]Some checks failed.[/] Fix them, then run "
            "[bold]mais doctor[/] again."
        )


@app.command()
def run(
    phase: str = typer.Argument(
        ..., help="research | order | eod | intraday | monitor | reflect"
    ),
    live: bool = typer.Option(False, "--live", help="Submit real orders (order phase only)"),
) -> None:
    """Run a single pipeline phase manually."""
    from scheduler.daily_pipeline import (
        eod_phase,
        intraday_stops_phase,
        monitor_unfilled_phase,
        order_phase,
        research_phase,
    )

    name = phase.lower().replace("-", "").replace("_", "")
    if name == "research":
        out = research_phase()
    elif name == "order":
        out = order_phase(dry_run=not live)
    elif name == "eod":
        out = eod_phase()
    elif name in ("intraday", "intradaystops"):
        out = intraday_stops_phase()
    elif name in ("monitor", "monitorunfilled"):
        out = monitor_unfilled_phase()
    elif name == "reflect":
        from agents.reflection_agent import ReflectionAgent

        out = {"path": str(ReflectionAgent().reflect(date.today()))}
    else:
        console.print(f"[red]Unknown phase:[/] {phase}")
        raise typer.Exit(code=2)
    console.print_json(json.dumps(out, ensure_ascii=False, default=str))


def main() -> None:
    if len(sys.argv) == 1:
        # Friendly default when invoked without args
        console.print("[bold cyan]MAI-System[/] — operator CLI")
        console.print("Try [bold]mais status[/], [bold]mais doctor[/], or [bold]mais --help[/].")
        return
    app()


if __name__ == "__main__":
    main()
