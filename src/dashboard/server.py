"""FastAPI backend for the Upbit trading dashboard.

Single-user by design: no accounts, no sessions, no multi-tenancy. It binds to
127.0.0.1 by default, and the only optional gate is a bearer token
(``UPBIT_DASHBOARD_TOKEN``) for when you tunnel it somewhere.

The API surface maps 1:1 onto the tabs of the SPA in ``static/``:

    /api/portfolio   포트폴리오      /api/trades     거래내역
    /api/analyses    분석내역        /api/config     전략
    /api/holdings    장기보유        /api/credentials 설정
    /api/control     실행 · 킬스위치
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from common.logging import get_logger, setup_logging
from upbit import credentials as creds_mod
from upbit import holdings as holdings_mod
from upbit import strategy as strategy_mod
from upbit.broker import PaperBroker
from upbit.client import UpbitAPIError, UpbitClient
from upbit.engine import KST, TradingEngine
from upbit.scheduler import get_scheduler
from upbit.store import get_store, utc_now

log = get_logger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
DASHBOARD_TOKEN = os.environ.get("UPBIT_DASHBOARD_TOKEN", "")

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Bring the scheduler up with the server, and take it down with it."""
    setup_logging()
    store = get_store()
    store.log_event("info", "dashboard", "대시보드 서버가 시작되었습니다.")
    if strategy_mod.load_config(store).schedule.enabled:
        try:
            get_scheduler().start()
        except Exception as exc:  # the UI must come up regardless
            log.error("upbit.scheduler.start_failed", error=str(exc))
            store.log_event("error", "scheduler", f"스케줄러 자동 시작 실패: {exc}")
    try:
        yield
    finally:
        with suppress(Exception):  # best-effort on the way out
            get_scheduler().stop()


app = FastAPI(
    title="Upbit Auto-Trading Dashboard",
    description="업비트 알트코인 데이트레이딩 자동매매 대시보드",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


# ----------------------------------------------------------------------
# Plumbing
# ----------------------------------------------------------------------
def require_token(request: Request) -> None:
    """No-op unless UPBIT_DASHBOARD_TOKEN is set (local-only deployments)."""
    if not DASHBOARD_TOKEN:
        return
    header = request.headers.get("authorization", "")
    if header.removeprefix("Bearer ").strip() != DASHBOARD_TOKEN:
        raise HTTPException(status_code=401, detail="대시보드 토큰이 올바르지 않습니다.")


Guarded = [Depends(require_token)]


def engine() -> TradingEngine:
    return get_scheduler().engine


@app.exception_handler(UpbitAPIError)
async def _upbit_error(_: Request, exc: UpbitAPIError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": f"업비트 API 오류: {exc}"})


@app.exception_handler(creds_mod.CredentialsError)
async def _creds_error(_: Request, exc: creds_mod.CredentialsError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ----------------------------------------------------------------------
# Health & credentials  (설정 탭)
# ----------------------------------------------------------------------
@app.get("/api/health", dependencies=Guarded)
def health() -> dict[str, Any]:
    store = get_store()
    config = strategy_mod.load_config(store)
    return {
        "status": "ok",
        "mode": config.mode,
        "credentials": creds_mod.status(),
        "scheduler": get_scheduler().status(),
        "server_time_kst": datetime.now(KST).isoformat(timespec="seconds"),
    }


class CredentialsPayload(BaseModel):
    access_key: str = Field(min_length=8)
    secret_key: str = Field(min_length=8)


@app.get("/api/credentials", dependencies=Guarded)
def get_credentials() -> dict[str, Any]:
    return creds_mod.status()


@app.post("/api/credentials", dependencies=Guarded)
def put_credentials(payload: CredentialsPayload) -> dict[str, Any]:
    creds_mod.save(payload.access_key, payload.secret_key)
    get_scheduler().reset_engine()
    return {"saved": True, **creds_mod.status()}


@app.delete("/api/credentials", dependencies=Guarded)
def drop_credentials() -> dict[str, Any]:
    removed = creds_mod.delete()
    get_scheduler().reset_engine()
    return {"deleted": removed, **creds_mod.status()}


@app.post("/api/credentials/test", dependencies=Guarded)
def test_credentials() -> dict[str, Any]:
    """Round-trip `/v1/accounts` to prove the key pair and IP allowlist work."""
    client = UpbitClient()
    try:
        accounts = client.get_accounts()
    except UpbitAPIError as exc:
        return {"ok": False, "message": f"인증 실패: {exc}", "hint": "업비트에서 IP 허용 목록을 확인하세요."}
    except creds_mod.CredentialsError as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        client.close()
    krw = next((a for a in accounts if a.get("currency") == "KRW"), {})
    return {
        "ok": True,
        "message": f"연결 성공 — 계좌 자산 {len(accounts)}종",
        "krw_balance": float(krw.get("balance") or 0),
        "currencies": sorted(str(a.get("currency")) for a in accounts),
    }


# ----------------------------------------------------------------------
# Portfolio  (포트폴리오 탭)
# ----------------------------------------------------------------------
@app.get("/api/portfolio", dependencies=Guarded)
def portfolio() -> dict[str, Any]:
    eng = engine()
    equity = eng.equity_view()
    stats = get_store().trade_stats(mode=eng.mode)
    return {
        "mode": eng.mode,
        "cash_krw": equity.cash_krw,
        "trading_krw": equity.trading_value_krw,
        "longterm_krw": equity.longterm_value_krw,
        "tradable_equity": equity.tradable_equity,
        "total_equity": equity.total_equity,
        "unrealized_pnl": equity.unrealized_pnl,
        "realized_pnl": stats["total_pnl"],
        "exposure_pct": equity.exposure_pct,
        "positions": equity.positions,
        "long_term": eng.long_term_view(),
        "open_positions": get_store().list_open_positions(eng.mode),
        "stats": stats,
    }


@app.get("/api/portfolio/history", dependencies=Guarded)
def portfolio_history(days: int = Query(90, ge=1, le=730)) -> dict[str, Any]:
    eng = engine()
    rows = get_store().equity_history(days=days, mode=eng.mode)
    return {"days": days, "mode": eng.mode, "points": rows}


@app.post("/api/portfolio/snapshot", dependencies=Guarded)
def take_snapshot() -> dict[str, Any]:
    return engine().snapshot_equity()


# ----------------------------------------------------------------------
# Trades  (거래내역 탭)
# ----------------------------------------------------------------------
@app.get("/api/trades", dependencies=Guarded)
def trades(
    limit: int = Query(300, ge=1, le=2000),
    market: str | None = None,
    side: str | None = Query(None, pattern="^(bid|ask)$"),
    days: int | None = Query(None, ge=1, le=3650),
    mode: str | None = Query(None, pattern="^(paper|live)$"),
) -> dict[str, Any]:
    since = (
        (utc_now() - timedelta(days=days)).isoformat(timespec="seconds") if days else None
    )
    store = get_store()
    rows = store.list_trades(
        limit=limit, market=market, side=side, since=since, mode=mode or engine().mode
    )
    return {"count": len(rows), "trades": rows}


@app.get("/api/trades/stats", dependencies=Guarded)
def trade_statistics(days: int | None = Query(None, ge=1, le=3650)) -> dict[str, Any]:
    eng = engine()
    store = get_store()
    return {
        "overall": store.trade_stats(mode=eng.mode),
        "window": store.trade_stats(mode=eng.mode, days=days) if days else None,
        "closed_positions": store.list_closed_positions(limit=200, mode=eng.mode),
    }


# ----------------------------------------------------------------------
# Analyses  (분석내역 탭)
# ----------------------------------------------------------------------
@app.get("/api/analyses/runs", dependencies=Guarded)
def analysis_runs(limit: int = Query(60, ge=1, le=365)) -> dict[str, Any]:
    return {"runs": get_store().list_runs(limit=limit, kind="selection")}


@app.get("/api/analyses/latest", dependencies=Guarded)
def latest_analysis(limit: int = Query(200, ge=1, le=500)) -> dict[str, Any]:
    store = get_store()
    run_id = store.latest_analysis_run_id()
    if run_id is None:
        return {"run": None, "items": []}
    return {
        "run": store.get_run(run_id),
        "items": store.list_analyses(run_id=run_id, limit=limit),
    }


@app.get("/api/analyses/{run_id}", dependencies=Guarded)
def analysis_detail(run_id: int, limit: int = Query(300, ge=1, le=500)) -> dict[str, Any]:
    store = get_store()
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="해당 분석 실행을 찾을 수 없습니다.")
    return {"run": run, "items": store.list_analyses(run_id=run_id, limit=limit)}


@app.get("/api/analyses/market/{market}", dependencies=Guarded)
def analysis_by_market(market: str, limit: int = Query(60, ge=1, le=365)) -> dict[str, Any]:
    return {"market": market, "history": get_store().list_analyses(market=market, limit=limit)}


# ----------------------------------------------------------------------
# Strategy config  (전략 탭)
# ----------------------------------------------------------------------
@app.get("/api/config", dependencies=Guarded)
def get_config() -> dict[str, Any]:
    store = get_store()
    return {
        "config": strategy_mod.load_config(store).model_dump(),
        "defaults": strategy_mod.yaml_defaults(),
        "overrides": store.get_override(strategy_mod.CONFIG_KEY, {}),
    }


@app.put("/api/config", dependencies=Guarded)
def put_config(patch: dict[str, Any]) -> dict[str, Any]:
    patch.pop("mode", None)  # mode has its own guarded endpoint
    try:
        config = strategy_mod.save_config(patch)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"설정 값이 올바르지 않습니다: {exc}") from exc
    get_scheduler().reschedule()
    return {"config": config.model_dump()}


@app.post("/api/config/reset", dependencies=Guarded)
def reset_config() -> dict[str, Any]:
    config = strategy_mod.reset_config()
    get_scheduler().reschedule()
    return {"config": config.model_dump()}


class ModePayload(BaseModel):
    mode: str = Field(pattern="^(paper|live)$")
    confirm: str = ""


@app.post("/api/config/mode", dependencies=Guarded)
def set_mode(payload: ModePayload) -> dict[str, Any]:
    """Live trading is opt-in and requires typing the confirmation phrase."""
    if payload.mode == "live":
        if payload.confirm != "I-UNDERSTAND":
            raise HTTPException(
                status_code=400,
                detail="실거래 전환에는 확인 문구 'I-UNDERSTAND' 가 필요합니다.",
            )
        if not creds_mod.load():
            raise HTTPException(status_code=400, detail="실거래 전환 전에 API 키를 등록하세요.")
    config = strategy_mod.set_mode(payload.mode)
    sched = get_scheduler()
    sched.reset_engine()
    sched.reschedule()
    return {"config": config.model_dump()}


# ----------------------------------------------------------------------
# Long-term holdings  (장기보유 탭)
# ----------------------------------------------------------------------
class HoldingPayload(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    locked_quantity: float = Field(default=0.0, ge=0)
    memo: str = ""


@app.get("/api/holdings", dependencies=Guarded)
def list_holdings() -> dict[str, Any]:
    store = get_store()
    items = [h.model_dump() for h in holdings_mod.list_holdings(store)]
    for item in items:
        item["protects_all"] = item["locked_quantity"] <= 0
    return {"holdings": items}


@app.post("/api/holdings", dependencies=Guarded)
def add_holding(payload: HoldingPayload) -> dict[str, Any]:
    try:
        holding = holdings_mod.add_holding(
            payload.symbol, payload.locked_quantity, payload.memo
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    get_scheduler().reset_engine()
    return {"holding": holding.model_dump()}


@app.delete("/api/holdings/{symbol}", dependencies=Guarded)
def delete_holding(symbol: str) -> dict[str, Any]:
    removed = holdings_mod.remove_holding(symbol)
    if not removed:
        raise HTTPException(status_code=404, detail=f"{symbol} 은(는) 장기보유 목록에 없습니다.")
    get_scheduler().reset_engine()
    return {"deleted": symbol}


@app.get("/api/holdings/suggest", dependencies=Guarded)
def suggest_holdings() -> dict[str, Any]:
    """Balances on the exchange that are not yet marked long-term."""
    eng = engine()
    existing = eng.guard.symbols
    out = []
    for acc in eng.broker.get_accounts():
        currency = str(acc.get("currency", "")).upper()
        balance = float(acc.get("balance") or 0) + float(acc.get("locked") or 0)
        if currency == "KRW" or balance <= 0 or currency in existing:
            continue
        out.append(
            {
                "symbol": currency,
                "balance": balance,
                "avg_buy_price": float(acc.get("avg_buy_price") or 0),
            }
        )
    return {"candidates": out}


# ----------------------------------------------------------------------
# Market data (charts)
# ----------------------------------------------------------------------
@app.get("/api/market/regime", dependencies=Guarded)
def market_regime() -> dict[str, Any]:
    from upbit import scoring

    eng = engine()
    candles = eng.client.get_day_candles("KRW-BTC", 120)
    return scoring.assess_regime(candles).model_dump(mode="json")


@app.get("/api/market/candles", dependencies=Guarded)
def market_candles(
    market: str = Query(..., min_length=3),
    unit: str = Query("day", pattern="^(day|1|3|5|10|15|30|60|240)$"),
    count: int = Query(120, ge=10, le=200),
) -> dict[str, Any]:
    client = engine().client
    raw = (
        client.get_day_candles(market, count)
        if unit == "day"
        else client.get_minute_candles(market, int(unit), count)
    )
    return {
        "market": market,
        "unit": unit,
        "candles": [
            {
                "time": c.get("candle_date_time_kst"),
                "open": c.get("opening_price"),
                "high": c.get("high_price"),
                "low": c.get("low_price"),
                "close": c.get("trade_price"),
                "volume": c.get("candle_acc_trade_volume"),
                "value": c.get("candle_acc_trade_price"),
            }
            for c in reversed(raw)
        ],
    }


# ----------------------------------------------------------------------
# Control  (실행 · 킬스위치)
# ----------------------------------------------------------------------
@app.get("/api/control/status", dependencies=Guarded)
def control_status() -> dict[str, Any]:
    store = get_store()
    return {
        "scheduler": get_scheduler().status(),
        "recent_runs": store.list_runs(limit=25),
        "events": store.list_events(limit=60),
    }


class RunPayload(BaseModel):
    dry_run: bool = False


@app.post("/api/control/run/selection", dependencies=Guarded)
def run_selection(payload: RunPayload) -> dict[str, Any]:
    return engine().run_selection(dry_run=payload.dry_run)


@app.post("/api/control/run/monitor", dependencies=Guarded)
def run_monitor() -> dict[str, Any]:
    return engine().monitor_positions()


@app.post("/api/control/scheduler/{action}", dependencies=Guarded)
def scheduler_control(action: str) -> dict[str, Any]:
    sched = get_scheduler()
    if action == "start":
        strategy_mod.save_config({"schedule": {"enabled": True}})
        return sched.start()
    if action == "stop":
        strategy_mod.save_config({"schedule": {"enabled": False}})
        return sched.stop()
    raise HTTPException(status_code=400, detail="action 은 start 또는 stop 이어야 합니다.")


class ClosePayload(BaseModel):
    position_id: int
    reason: str = "manual"


@app.post("/api/control/close", dependencies=Guarded)
def close_position(payload: ClosePayload) -> dict[str, Any]:
    result = engine().close_position_by_id(payload.position_id, payload.reason)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class PanicPayload(BaseModel):
    confirm: str


@app.post("/api/control/panic", dependencies=Guarded)
def panic_sell(payload: PanicPayload) -> dict[str, Any]:
    """Kill switch. Flattens engine positions only — 장기보유 코인은 보호된다."""
    if payload.confirm != "I-UNDERSTAND":
        raise HTTPException(
            status_code=400, detail="전량 청산에는 확인 문구 'I-UNDERSTAND' 가 필요합니다."
        )
    return engine().liquidate_all(reason="panic")


class PaperPayload(BaseModel):
    initial_krw: float = Field(default=10_000_000, gt=0)


@app.post("/api/control/paper/reset", dependencies=Guarded)
def reset_paper(payload: PaperPayload) -> dict[str, Any]:
    eng = engine()
    if not isinstance(eng.broker, PaperBroker):
        raise HTTPException(status_code=400, detail="모의 계좌 초기화는 paper 모드에서만 가능합니다.")
    return eng.broker.reset(payload.initial_krw)


@app.get("/api/logs", dependencies=Guarded)
def logs(
    limit: int = Query(200, ge=1, le=1000),
    level: str | None = Query(None, pattern="^(info|warning|error)$"),
) -> dict[str, Any]:
    return {"events": get_store().list_events(limit=limit, level=level)}


# ----------------------------------------------------------------------
# Static SPA
# ----------------------------------------------------------------------
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))


def main() -> None:
    """`mais-upbit-dashboard` entry point."""
    import uvicorn

    from upbit.doctor import dashboard_address, port_in_use

    host, port = dashboard_address()
    if port_in_use(host if host != "0.0.0.0" else "127.0.0.1", port):
        # Binding would fail with a bare errno; say something useful instead.
        print(
            f"\n[중단] {host}:{port} 를 이미 다른 프로세스가 사용하고 있습니다.\n"
            f"       대시보드가 이미 떠 있다면 http://{host}:{port} 를 여세요.\n"
            f"       새로 띄우려면 다른 포트를 쓰세요:  mais-upbit serve --port {port + 1}\n",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # The container healthcheck hits /api/health every 30s; its access lines add
    # ~3k rows a day and say nothing the event log doesn't. Keep them only when
    # someone is actually debugging.
    debug = os.environ.get("MAIS_LOG_LEVEL", "INFO").upper() == "DEBUG"
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="debug" if debug else "info",
        access_log=debug,
    )


if __name__ == "__main__":
    main()
