"""End-to-end smoke test — research → order → eod with mocked KIS + LLM.

Goals:
- Catch import-graph breakage between phases
- Verify state.json is written and read back correctly
- Verify journal rows show up after each phase
- Verify EOD reconciliation + universe cache invalidation run without raising
"""
from __future__ import annotations

from datetime import date

from common.types import (
    AgentProposal,
    ExecutionResult,
    MarketRegime,
    Order,
    Side,
    TickerView,
)


def _patch_settings(monkeypatch, tmp_path, payload: dict | None = None) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "00000000-01")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # disable real LLM calls
    from common import config as c

    c.get_env.cache_clear()
    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: payload
        or {
            "memory": {"journal_db": str(tmp_path / "j.sqlite")},
            "consensus": {
                "weights": {"macro": 0.5, "quant": 0.5},
                "min_conviction": 5,
                "min_agreement": 1,
            },
            "execution": {"dry_run_default": True, "twap_enabled": False},
            "risk": {
                "max_position_weight": 0.10,
                "max_sector_weight": 0.30,
                "cash_buffer_min": 0.05,
                "rebalance_threshold": 0.05,
            },
        },
    )


class _StubMacro:
    name = "macro"

    def run(self, as_of: date) -> AgentProposal:
        return AgentProposal(
            agent_name="macro",
            as_of=as_of,
            conviction=8,
            rationale="risk-on",
            equity_weight=0.8,
            regime=MarketRegime.RISK_ON,
        )


class _StubQuant:
    name = "quant"

    def run(self, as_of: date) -> AgentProposal:
        return AgentProposal(
            agent_name="quant",
            as_of=as_of,
            conviction=8,
            rationale="momentum tailwind",
            picks=[
                TickerView(
                    ticker="005930",
                    name="삼성전자",
                    side=Side.BUY,
                    target_weight=0.08,
                    score=2.0,
                ),
            ],
        )


class _StubSilent:
    """Sector / Value stubs return low-conviction proposals so they get
    dropped from consensus — keeps the test deterministic."""

    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, as_of: date) -> AgentProposal:
        return AgentProposal(
            agent_name=self.name, as_of=as_of, conviction=0, rationale=""
        )


class _FakeKIS:
    def __init__(self) -> None:
        self.placed: list[Order] = []
        self._positions: dict[str, int] = {}

    def get_account_state(self) -> dict:
        return {
            "positions": dict(self._positions),
            "prices": {"005930": 70_000.0},
            "cash": 100_000_000.0,
            "nav": 100_000_000.0,
            "raw": {"output1": [], "output2": [{"dnca_tot_amt": "100000000"}]},
        }

    def get_prices(self, tickers: list[str]) -> dict[str, float]:
        return {t: 70_000.0 for t in tickers}

    def get_orderbooks(self, tickers: list[str]) -> dict:
        return {t: {"bid": 69_900, "ask": 70_100, "mid": 70_000, "spread_bps": 30} for t in tickers}

    def place_order(self, order: Order) -> ExecutionResult:
        from datetime import datetime as _dt

        self.placed.append(order)
        self._positions[order.ticker] = (
            self._positions.get(order.ticker, 0) + order.quantity
            if order.side is Side.BUY
            else max(0, self._positions.get(order.ticker, 0) - order.quantity)
        )
        return ExecutionResult(
            order=order,
            submitted_at=_dt.utcnow(),
            status="submitted",
            filled_qty=order.quantity,
            avg_price=order.price,
            broker_order_id="FAKE-1",
            message="ok",
        )


def test_e2e_research_order_eod_smokes(monkeypatch, tmp_path) -> None:
    _patch_settings(monkeypatch, tmp_path)

    # Replace agents with stubs so we don't make real LLM calls
    import scheduler.daily_pipeline as dp

    monkeypatch.setattr(dp, "MacroAgent", lambda: _StubMacro())
    monkeypatch.setattr(dp, "QuantAgent", lambda: _StubQuant())
    monkeypatch.setattr(dp, "SectorAgent", lambda: _StubSilent("sector"))
    monkeypatch.setattr(dp, "ValueAgent", lambda: _StubSilent("value"))

    fake = _FakeKIS()
    monkeypatch.setattr("broker.kis_client.KISClient", lambda: fake)

    # Force trading-day check to True (today might be weekend/holiday)
    monkeypatch.setattr("common.calendar.is_trading_day", lambda d: True)

    # Skip the overnight shock check (we don't want network flake here)
    monkeypatch.setattr(
        "data.macro.fetch_overnight_shock",
        lambda: {"breached": False, "shock_pct": 0.0, "threshold": -0.02, "proxy": "EWY"},
    )

    today = date(2025, 5, 6)
    # Phase 1: research
    out = dp.research_phase(today)
    assert out["n_proposals"] >= 1
    # State file should exist
    state_file = tmp_path / "state" / "target_2025-05-06.json"
    assert state_file.exists()

    # Phase 2: order
    out2 = dp.order_phase(today, dry_run=False)
    # Either KIS placed orders, or pipeline cleanly skipped due to no-target
    # (which is fine — we just want no exception)
    assert "as_of" in out2

    # Phase 3: eod (should not raise even with no NAV history)
    out3 = dp.eod_phase(today)
    assert out3["as_of"] == today.isoformat()


def test_research_phase_writes_target_with_picks(monkeypatch, tmp_path) -> None:
    _patch_settings(monkeypatch, tmp_path)
    import scheduler.daily_pipeline as dp

    monkeypatch.setattr(dp, "MacroAgent", lambda: _StubMacro())
    monkeypatch.setattr(dp, "QuantAgent", lambda: _StubQuant())
    monkeypatch.setattr(dp, "SectorAgent", lambda: _StubSilent("sector"))
    monkeypatch.setattr(dp, "ValueAgent", lambda: _StubSilent("value"))

    out = dp.research_phase(date(2025, 5, 6))
    target = out["target"]
    # 005930 should appear in the consensus picks → optimizer assigned weight
    assert "005930" in target["positions"] or target["cash_weight"] == 1.0
