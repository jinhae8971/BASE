"""`mais-upbit` — headless control for the Upbit subsystem.

Everything the dashboard does, minus the browser: useful for cron, for a first
smoke test, and for driving the system on a box with no UI.

    mais-upbit scan --dry-run          # score the universe, place no orders
    mais-upbit scan                    # scan and enter per the current strategy
    mais-upbit monitor                 # run one exit-check pass
    mais-upbit status                  # equity, positions, mode, schedule
    mais-upbit holdings add BTC        # protect a coin from the engine
    mais-upbit serve                   # start the dashboard + scheduler
"""
from __future__ import annotations

import json
from typing import Any

import typer

from common.logging import setup_logging

app = typer.Typer(add_completion=False, help="업비트 자동매매 CLI")
holdings_app = typer.Typer(add_completion=False, help="장기보유 코인 관리")
app.add_typer(holdings_app, name="holdings")


def _echo(payload: Any) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


@app.command()
def scan(
    dry_run: bool = typer.Option(
        True, "--dry-run/--execute", help="기본값은 시뮬레이션 — 실제 주문은 --execute"
    ),
) -> None:
    """유니버스를 스캔하고 종합점수 상위 종목에 진입합니다."""
    setup_logging()
    from .engine import TradingEngine

    _echo(TradingEngine().run_selection(dry_run=dry_run))


@app.command()
def monitor(
    force_exit: bool = typer.Option(False, "--force-exit", help="모든 포지션을 강제 청산"),
) -> None:
    """보유 포지션의 익절·손절·트레일링·보유시간 조건을 점검합니다."""
    setup_logging()
    from .engine import TradingEngine

    _echo(TradingEngine().monitor_positions(force_exit=force_exit))


@app.command()
def status() -> None:
    """현재 모드·자산·포지션·스케줄 상태를 출력합니다."""
    setup_logging()
    from . import credentials as creds_mod
    from .engine import TradingEngine
    from .store import get_store

    engine = TradingEngine()
    equity = engine.equity_view()
    store = get_store()
    _echo(
        {
            "mode": engine.mode,
            "credentials": creds_mod.status(),
            "equity": {
                "total_krw": equity.total_equity,
                "cash_krw": equity.cash_krw,
                "trading_krw": equity.trading_value_krw,
                "longterm_krw": equity.longterm_value_krw,
                "unrealized_pnl": equity.unrealized_pnl,
                "exposure_pct": equity.exposure_pct,
            },
            "open_positions": store.list_open_positions(engine.mode),
            "protected_symbols": sorted(engine.guard.symbols),
            "stats": store.trade_stats(mode=engine.mode),
        }
    )


@app.command()
def liquidate(
    confirm: str = typer.Option(..., "--confirm", help="'I-UNDERSTAND' 를 입력해야 실행됩니다."),
) -> None:
    """킬 스위치 — 자동매매 포지션을 전량 청산합니다 (장기보유 제외)."""
    if confirm != "I-UNDERSTAND":
        typer.echo("확인 문구가 필요합니다: --confirm I-UNDERSTAND", err=True)
        raise typer.Exit(code=1)
    setup_logging()
    from .engine import TradingEngine

    _echo(TradingEngine().liquidate_all(reason="panic"))


@app.command()
def serve(
    host: str = typer.Option("", help="기본값은 settings.yaml 의 upbit.dashboard.host"),
    port: int = typer.Option(0, help="기본값은 settings.yaml 의 upbit.dashboard.port"),
) -> None:
    """대시보드와 스케줄러를 함께 실행합니다."""
    import os

    if host:
        os.environ["UPBIT_DASHBOARD_HOST"] = host
    if port:
        os.environ["UPBIT_DASHBOARD_PORT"] = str(port)
    from dashboard.server import main as serve_main

    serve_main()


@holdings_app.command("list")
def holdings_list() -> None:
    """장기보유(거래 제외) 코인 목록을 출력합니다."""
    from .holdings import list_holdings

    _echo([h.model_dump() for h in list_holdings()])


@holdings_app.command("add")
def holdings_add(
    symbol: str,
    locked_quantity: float = typer.Option(0.0, help="0 이면 보유 전량 보호"),
    memo: str = typer.Option("", help="메모"),
) -> None:
    """코인을 자동매매 대상에서 제외합니다."""
    from .holdings import add_holding

    _echo(add_holding(symbol, locked_quantity, memo).model_dump())


@holdings_app.command("remove")
def holdings_remove(symbol: str) -> None:
    """장기보유 제외를 해제합니다."""
    from .holdings import remove_holding

    _echo({"removed": remove_holding(symbol), "symbol": symbol.upper()})


def main() -> None:
    app()


if __name__ == "__main__":
    main()
