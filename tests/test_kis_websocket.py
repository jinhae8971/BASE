"""KIS H0STCNT0 frame parsing + tick handling logic.

We can't unit-test the real websocket session here, but we can verify the
synchronous tick-dispatch logic that decides whether to fire stops.
"""
from __future__ import annotations

from broker.kis_websocket import RealtimeStops


def _patch(monkeypatch) -> RealtimeStops:
    from common import config as c

    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {
            "websocket": {
                "tick_threshold": 0.015,
                "cooldown_seconds": 60,
            },
        },
    )
    return RealtimeStops()


def test_first_tick_only_seeds_reference(monkeypatch) -> None:
    rt = _patch(monkeypatch)
    fired: list[str] = []
    monkeypatch.setattr(
        "scheduler.daily_pipeline.intraday_stops_phase",
        lambda: fired.append("yes"),
    )
    rt.handle_tick("005930", 70_000.0)
    assert fired == []
    assert rt._reference["005930"] == 70_000.0


def test_small_move_does_not_fire(monkeypatch) -> None:
    rt = _patch(monkeypatch)
    rt.handle_tick("005930", 70_000.0)  # seed
    fired: list[str] = []
    monkeypatch.setattr(
        "scheduler.daily_pipeline.intraday_stops_phase",
        lambda: fired.append("yes"),
    )
    rt.handle_tick("005930", 70_500.0)  # +0.7%, below threshold
    assert fired == []


def test_threshold_breach_fires(monkeypatch) -> None:
    rt = _patch(monkeypatch)
    rt.handle_tick("005930", 70_000.0)  # seed
    fired: list[str] = []
    monkeypatch.setattr(
        "scheduler.daily_pipeline.intraday_stops_phase",
        lambda: fired.append("yes"),
    )
    rt.handle_tick("005930", 68_500.0)  # -2.1%, above 1.5% threshold
    assert fired == ["yes"]
    # Reference advanced
    assert rt._reference["005930"] == 68_500.0


def test_cooldown_suppresses_back_to_back_fires(monkeypatch) -> None:
    rt = _patch(monkeypatch)
    rt.handle_tick("005930", 70_000.0)  # seed
    fired: list[str] = []
    monkeypatch.setattr(
        "scheduler.daily_pipeline.intraday_stops_phase",
        lambda: fired.append("yes"),
    )
    rt.handle_tick("005930", 68_500.0)  # first fire
    rt.handle_tick("005930", 67_000.0)  # within cooldown — must NOT refire
    assert fired == ["yes"]


def test_dispatch_parses_h0stcnt0_frame(monkeypatch) -> None:
    rt = _patch(monkeypatch)
    captured: list[tuple[str, float]] = []
    monkeypatch.setattr(
        rt, "handle_tick", lambda t, p: captured.append((t, p))
    )
    # Simulated H0STCNT0 frame: 0|H0STCNT0|001|005930^120000^70000^...
    rt._dispatch("0|H0STCNT0|001|005930^120000^70000^0^0^0")
    assert captured == [("005930", 70_000.0)]


def test_dispatch_ignores_non_h0stcnt0(monkeypatch) -> None:
    rt = _patch(monkeypatch)
    captured: list[tuple[str, float]] = []
    monkeypatch.setattr(
        rt, "handle_tick", lambda t, p: captured.append((t, p))
    )
    rt._dispatch("PINGPONG")
    rt._dispatch("0|H0STASP0|001|005930^...")  # different TR
    assert captured == []


def test_dispatch_robust_to_malformed_payload(monkeypatch) -> None:
    rt = _patch(monkeypatch)
    # Wrong number of fields, non-numeric price — must not raise
    rt._dispatch("0|H0STCNT0|001|short")
    rt._dispatch("0|H0STCNT0|001|005930^abc^xyz")
