"""Dashboard API — the contract the SPA depends on.

The store, scheduler and credential paths are all redirected into tmp_path so a
test run never reads or writes the real ``data_store/``.
"""
from __future__ import annotations

import pytest
from upbit_fakes import FakeUpbitClient

fastapi_testclient = pytest.importorskip("fastapi.testclient")

MARKETS = ["KRW-BTC", "KRW-XRP", "KRW-DOGE", "KRW-SOL"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from upbit import credentials as creds_mod
    from upbit import scheduler as sched_mod
    from upbit import store as store_mod
    from upbit.broker import PaperBroker
    from upbit.engine import TradingEngine

    store = store_mod.UpbitStore(tmp_path / "upbit.sqlite")
    # The scheduler must not actually arm cron jobs during a test run.
    store.set_override("config", {"schedule": {"enabled": False}})
    monkeypatch.setattr(store_mod, "_STORE", store)

    monkeypatch.setattr(creds_mod, "_credentials_path", lambda: tmp_path / "creds.enc")
    monkeypatch.setattr(creds_mod, "_master_key_path", lambda: tmp_path / ".master.key")

    fake = FakeUpbitClient(
        MARKETS,
        profiles={m: {"drift": 0.010, "wobble": 0.006} for m in MARKETS},
        prices={"KRW-BTC": 90_000_000, "KRW-XRP": 1500, "KRW-DOGE": 300, "KRW-SOL": 250_000},
    )
    scheduler = sched_mod.UpbitScheduler(store)
    scheduler._engine = TradingEngine(
        store=store, client=fake, broker=PaperBroker(fake, store, initial_krw=10_000_000)
    )
    monkeypatch.setattr(sched_mod, "_SCHEDULER", scheduler)

    from dashboard import server

    with fastapi_testclient.TestClient(server.app) as test_client:
        test_client.store = store
        test_client.fake = fake
        yield test_client


def test_health_reports_paper_mode(client) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["mode"] == "paper"
    assert body["credentials"]["configured"] is False


def test_health_is_503_when_not_actually_trading(client, monkeypatch) -> None:
    """HTTP 200 must not mean 'healthy' when the scheduler stopped managing
    positions — the container healthcheck reads this endpoint."""
    from upbit import scheduler as sched_mod

    sched = sched_mod._SCHEDULER
    monkeypatch.setattr(
        sched, "health",
        lambda: {"healthy": False, "problems": ["모니터링이 42분째 성공하지 못했습니다."],
                 "running": False, "started_at": None, "uptime_sec": 0,
                 "watchdog_alive": False, "watchdog_revivals": 0, "cycles": {}},
    )
    resp = client.get("/api/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert "모니터링" in body["problems"][0]


def test_health_carries_version_and_storage(client) -> None:
    body = client.get("/api/health").json()
    assert body["version"]
    assert body["storage"]["db_bytes"] > 0
    assert "open_positions" in body["storage"]


def test_portfolio_shape(client) -> None:
    body = client.get("/api/portfolio").json()
    for key in ("cash_krw", "trading_krw", "longterm_krw", "total_equity", "positions", "stats"):
        assert key in body
    assert body["cash_krw"] == pytest.approx(10_000_000)


def test_credentials_are_masked_never_echoed(client) -> None:
    access, secret = "AAAA1111BBBB2222", "SSSS3333TTTT4444"
    saved = client.post(
        "/api/credentials", json={"access_key": access, "secret_key": secret}
    ).json()
    assert saved["configured"] is True
    assert saved["access_key"] != access
    assert saved["access_key"].startswith("AAAA") and saved["access_key"].endswith("2222")
    assert secret not in client.get("/api/credentials").text

    assert client.delete("/api/credentials").json()["deleted"] is True
    assert client.get("/api/credentials").json()["configured"] is False


def test_credentials_reject_short_input(client) -> None:
    assert client.post("/api/credentials", json={"access_key": "x", "secret_key": "y"}).status_code == 422


def test_config_round_trip(client) -> None:
    before = client.get("/api/config").json()["config"]
    assert before["mode"] == "paper"

    updated = client.put(
        "/api/config", json={"strategy": {"max_positions": 7, "min_score": 71.5}}
    ).json()["config"]
    assert updated["strategy"]["max_positions"] == 7
    assert updated["strategy"]["min_score"] == pytest.approx(71.5)

    # Persisted, not just echoed.
    assert client.get("/api/config").json()["config"]["strategy"]["max_positions"] == 7

    client.post("/api/config/reset")
    assert client.get("/api/config").json()["config"]["strategy"]["max_positions"] == 5


def test_config_rejects_invalid_values(client) -> None:
    assert client.put("/api/config", json={"strategy": {"max_positions": 0}}).status_code == 422
    assert client.put("/api/config", json={"schedule": {"selection_time": "25:99"}}).status_code == 422


def test_config_put_cannot_flip_the_mode(client) -> None:
    client.put("/api/config", json={"mode": "live"})
    assert client.get("/api/config").json()["config"]["mode"] == "paper"


def test_live_mode_requires_confirmation_and_keys(client) -> None:
    assert client.post("/api/config/mode", json={"mode": "live"}).status_code == 400
    # Right phrase, but no API keys registered yet.
    resp = client.post("/api/config/mode", json={"mode": "live", "confirm": "I-UNDERSTAND"})
    assert resp.status_code == 400
    assert "API 키" in resp.json()["detail"]
    assert client.get("/api/config").json()["config"]["mode"] == "paper"


def test_holdings_crud(client) -> None:
    assert client.get("/api/holdings").json()["holdings"] == []

    client.post("/api/holdings", json={"symbol": "btc", "locked_quantity": 0, "memo": "장투"})
    client.post("/api/holdings", json={"symbol": "KRW-ETH", "locked_quantity": 1.5})
    holdings = client.get("/api/holdings").json()["holdings"]
    assert {h["symbol"] for h in holdings} == {"BTC", "ETH"}
    assert next(h for h in holdings if h["symbol"] == "BTC")["protects_all"] is True
    assert next(h for h in holdings if h["symbol"] == "ETH")["protects_all"] is False

    assert client.delete("/api/holdings/BTC").status_code == 200
    assert client.delete("/api/holdings/BTC").status_code == 404


def test_holdings_reject_krw(client) -> None:
    assert client.post("/api/holdings", json={"symbol": "KRW"}).status_code == 400


def test_scan_then_read_the_analysis_back(client) -> None:
    result = client.post("/api/control/run/selection", json={"dry_run": True}).json()
    assert "error" not in result
    assert result["scanned"] > 0

    latest = client.get("/api/analyses/latest").json()
    assert latest["run"]["id"] == result["run_id"]
    assert len(latest["items"]) == result["scanned"]

    item = latest["items"][0]
    for key in ("total_score", "volume_score", "flow_score", "tech_score", "beta_score", "metrics"):
        assert key in item

    runs = client.get("/api/analyses/runs").json()["runs"]
    assert runs and runs[0]["id"] == result["run_id"]
    assert client.get(f"/api/analyses/{result['run_id']}").json()["run"]["id"] == result["run_id"]


def test_unknown_analysis_run_is_404(client) -> None:
    assert client.get("/api/analyses/99999").status_code == 404


def test_scan_excludes_long_term_holdings(client) -> None:
    client.post("/api/holdings", json={"symbol": "XRP"})
    result = client.post("/api/control/run/selection", json={"dry_run": True}).json()
    markets = {i["market"] for i in client.get("/api/analyses/latest").json()["items"]}
    assert "KRW-XRP" not in markets
    assert result["scanned"] > 0


def test_panic_requires_the_phrase(client) -> None:
    assert client.post("/api/control/panic", json={"confirm": "yes"}).status_code == 400
    body = client.post("/api/control/panic", json={"confirm": "I-UNDERSTAND"}).json()
    assert body["requested"] == 0


def test_trades_and_stats_endpoints(client) -> None:
    assert client.get("/api/trades").json()["count"] == 0
    stats = client.get("/api/trades/stats").json()
    assert stats["overall"]["closed_trades"] == 0

    client.post("/api/control/run/selection", json={"dry_run": False})
    trades = client.get("/api/trades").json()
    assert trades["count"] > 0
    assert all(t["side"] == "bid" for t in trades["trades"])
    assert client.get("/api/trades?side=ask").json()["count"] == 0


def test_snapshot_and_history(client) -> None:
    client.post("/api/portfolio/snapshot")
    points = client.get("/api/portfolio/history?days=7").json()["points"]
    assert len(points) == 1
    assert points[0]["total_krw"] > 0


def test_paper_reset(client) -> None:
    client.post("/api/control/run/selection", json={"dry_run": False})
    assert client.get("/api/portfolio").json()["cash_krw"] < 10_000_000
    client.post("/api/control/paper/reset", json={"initial_krw": 5_000_000})
    assert client.get("/api/portfolio").json()["cash_krw"] == pytest.approx(5_000_000)


def test_market_endpoints(client) -> None:
    regime = client.get("/api/market/regime").json()
    assert regime["regime"] in ("risk_on", "neutral", "risk_off")

    candles = client.get("/api/market/candles?market=KRW-XRP&unit=day&count=30").json()
    assert len(candles["candles"]) > 0
    assert set(candles["candles"][0]) >= {"time", "open", "high", "low", "close", "volume"}


def test_logs_endpoint_filters_by_level(client) -> None:
    client.store.log_event("error", "test", "문제 발생")
    client.store.log_event("info", "test", "정상")
    assert len(client.get("/api/logs?level=error").json()["events"]) == 1


def test_index_serves_the_spa(client) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "UPBIT DESK" in resp.text
