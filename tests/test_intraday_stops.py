from __future__ import annotations

from datetime import date

from common.types import ExecutionResult, Order, Side
from portfolio.position_state import upsert_on_buy
from scheduler.daily_pipeline import intraday_stops_phase


class _FakeKIS:
    """Minimal KISClient stub — record calls + return canned data."""

    def __init__(self, positions: dict[str, int], prices: dict[str, float]) -> None:
        self._positions = positions
        self._prices = prices
        self.placed: list[Order] = []

    def get_account_state(self) -> dict:
        return {
            "positions": self._positions,
            "prices": self._prices,
            "cash": 10_000_000,
            "nav": 10_000_000 + sum(
                self._positions[t] * self._prices[t] for t in self._positions
            ),
            "raw": {},
        }

    def get_prices(self, tickers: list[str]) -> dict[str, float]:
        return {t: self._prices[t] for t in tickers if t in self._prices}

    def place_order(self, order: Order) -> ExecutionResult:
        from datetime import datetime as _dt

        self.placed.append(order)
        return ExecutionResult(
            order=order,
            submitted_at=_dt.utcnow(),
            status="submitted",
            filled_qty=order.quantity,
            avg_price=order.price,
            broker_order_id="FAKE-1",
            message="ok",
        )


def _patch_settings(monkeypatch, payload: dict) -> None:
    from common import config as c

    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr("common.config.load_yaml_settings", lambda: payload)


def test_intraday_phase_no_kis_skips(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KIS_APP_KEY", "")
    monkeypatch.setenv("KIS_APP_SECRET", "")
    from common import config as c

    c.get_env.cache_clear()
    out = intraday_stops_phase(date(2025, 5, 6))
    assert out.get("skipped") == "no KIS credentials"


def test_intraday_phase_fires_market_sell_below_hard_stop(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "00000000-01")
    from common import config as c

    c.get_env.cache_clear()
    _patch_settings(
        monkeypatch,
        {
            "memory": {"journal_db": str(tmp_path / "j.sqlite")},
            "risk": {
                "hard_stop_pct": 0.12,
                "trailing_take_pct": 0.10,
                "trailing_min_profit": 0.05,
            },
        },
    )

    # Record an entry at 100k for ticker 005930
    upsert_on_buy("005930", 100_000, 50, date(2025, 5, 1))

    fake = _FakeKIS(positions={"005930": 50}, prices={"005930": 85_000})
    monkeypatch.setattr("broker.kis_client.KISClient", lambda: fake)

    out = intraday_stops_phase(date(2025, 5, 6))
    assert out["n_stops"] == 1
    assert len(fake.placed) == 1
    placed = fake.placed[0]
    assert placed.side is Side.SELL
    assert placed.order_type == "market"
    assert placed.quantity == 50


def test_intraday_phase_no_stops_when_safe(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "00000000-01")
    from common import config as c

    c.get_env.cache_clear()
    _patch_settings(
        monkeypatch,
        {
            "memory": {"journal_db": str(tmp_path / "j.sqlite")},
            "risk": {
                "hard_stop_pct": 0.12,
                "trailing_take_pct": 0.10,
                "trailing_min_profit": 0.05,
            },
        },
    )

    upsert_on_buy("005930", 100_000, 50, date(2025, 5, 1))
    # Up 5% — neither hard stop nor trailing take fires
    fake = _FakeKIS(positions={"005930": 50}, prices={"005930": 105_000})
    monkeypatch.setattr("broker.kis_client.KISClient", lambda: fake)

    out = intraday_stops_phase(date(2025, 5, 6))
    assert out["n_stops"] == 0
    assert fake.placed == []


def test_intraday_phase_no_positions(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "00000000-01")
    from common import config as c

    c.get_env.cache_clear()

    fake = _FakeKIS(positions={}, prices={})
    monkeypatch.setattr("broker.kis_client.KISClient", lambda: fake)

    out = intraday_stops_phase(date(2025, 5, 6))
    assert out["n_positions"] == 0
    assert out["n_stops"] == 0
