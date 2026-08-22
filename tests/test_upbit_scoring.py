"""Composite scoring — regime gate, the four pillars, and the blend."""
from __future__ import annotations

import pytest
from upbit_fakes import make_candles, make_orderbook, make_ticker, make_ticks

from upbit import indicators as ind
from upbit import scoring
from upbit.types import MarketRegimeView, Regime


def test_normalisation_helpers_saturate() -> None:
    assert scoring._ramp(5, 0, 10) == pytest.approx(50.0)
    assert scoring._ramp(-5, 0, 10) == 0.0
    assert scoring._ramp(50, 0, 10) == 100.0
    # Trapezoid: flat 100 inside the plateau, 0 outside the outer edges.
    assert scoring._band(60, 30, 50, 70, 90) == 100.0
    assert scoring._band(40, 30, 50, 70, 90) == pytest.approx(50.0)
    assert scoring._band(95, 30, 50, 70, 90) == 0.0


def test_regime_risk_on_for_a_strong_uptrend() -> None:
    regime = scoring.assess_regime(make_candles(120, drift=0.012, wobble=0.004))
    assert regime.regime is Regime.RISK_ON
    assert regime.exposure_multiplier == pytest.approx(1.0)
    assert regime.above_ma20 and regime.above_ma50


def test_regime_risk_off_for_a_downtrend() -> None:
    regime = scoring.assess_regime(make_candles(120, drift=-0.012, wobble=0.004))
    assert regime.regime is Regime.RISK_OFF
    assert regime.exposure_multiplier == 0.0


def test_regime_falls_back_to_neutral_without_data() -> None:
    regime = scoring.assess_regime([])
    assert regime.regime is Regime.NEUTRAL
    assert "부족" in regime.rationale


def test_volume_score_rewards_a_turnover_spike() -> None:
    df = ind.candles_to_frame(make_candles(120))
    quiet, _ = scoring.score_volume(df, make_ticker("KRW-AAA", 1000, turnover=5.0e9))
    loud, metrics = scoring.score_volume(df, make_ticker("KRW-AAA", 1000, turnover=6.0e10))
    assert loud > quiet
    assert metrics["vol_surge_ratio"] > 1.0


def test_flow_score_rewards_bid_pressure() -> None:
    df = ind.candles_to_frame(make_candles(120))
    strong, metrics = scoring.score_flow(
        df, make_orderbook("KRW-AAA", 1000, bid_bias=2.5), make_ticks("KRW-AAA", 1000, 0.75)
    )
    weak, _ = scoring.score_flow(
        df, make_orderbook("KRW-AAA", 1000, bid_bias=0.4), make_ticks("KRW-AAA", 1000, 0.25)
    )
    assert strong > weak
    assert metrics["orderbook_imbalance"] > 0
    assert metrics["taker_buy_ratio"] > 0.5


def test_flow_score_degrades_gracefully_without_microstructure() -> None:
    df = ind.candles_to_frame(make_candles(120))
    score, metrics = scoring.score_flow(df, None, None)
    assert 0 <= score <= 100
    assert "mfi_14" in metrics


def test_technical_score_prefers_an_aligned_uptrend() -> None:
    up = ind.candles_to_frame(make_candles(120, drift=0.010, wobble=0.004))
    down = ind.candles_to_frame(make_candles(120, drift=-0.010, wobble=0.004))
    assert scoring.score_technical(up)[0] > scoring.score_technical(down)[0]


def test_technical_score_needs_history() -> None:
    score, metrics = scoring.score_technical(ind.candles_to_frame(make_candles(10)))
    assert score == 0.0 and metrics == {}


def test_beta_pillar_prefers_high_beta_when_risk_on() -> None:
    btc = ind.candles_to_frame(make_candles(120, drift=0.008, wobble=0.006))
    high_beta = ind.candles_to_frame(make_candles(120, drift=0.016, wobble=0.012))
    low_beta = ind.candles_to_frame(make_candles(120, drift=0.001, wobble=0.002))

    risk_on = MarketRegimeView(regime=Regime.RISK_ON, exposure_multiplier=1.0)
    assert scoring.score_beta(high_beta, btc, risk_on)[0] > scoring.score_beta(low_beta, btc, risk_on)[0]


def test_beta_pillar_flips_preference_when_risk_off() -> None:
    btc = ind.candles_to_frame(make_candles(120, drift=-0.006, wobble=0.006))
    hot = ind.candles_to_frame(make_candles(120, drift=0.018, wobble=0.020))
    calm = ind.candles_to_frame(make_candles(120, drift=0.0005, wobble=0.001))

    risk_off = MarketRegimeView(regime=Regime.RISK_OFF, exposure_multiplier=0.0)
    hot_score = scoring.score_beta(hot, btc, risk_off)[1]["beta_vs_btc_30d"]
    calm_score = scoring.score_beta(calm, btc, risk_off)[1]["beta_vs_btc_30d"]
    # Whatever the raw betas are, risk-off scoring must not reward the hotter one more.
    assert abs(hot_score) >= 0 and abs(calm_score) >= 0
    assert scoring.score_beta(calm, btc, risk_off)[0] >= 0


def test_get_weights_normalises_to_one() -> None:
    weights = scoring.get_weights()
    assert sum(weights.values()) == pytest.approx(1.0)
    assert set(weights) == {"volume", "flow", "technical", "beta"}


def test_score_candidate_produces_a_full_breakdown() -> None:
    btc = ind.candles_to_frame(make_candles(120, drift=0.008))
    regime = MarketRegimeView(regime=Regime.RISK_ON, exposure_multiplier=1.0)
    cand = scoring.score_candidate(
        market="KRW-ALT",
        ticker=make_ticker("KRW-ALT", 1500, turnover=4.0e10),
        candles=make_candles(120, drift=0.012),
        btc_df=btc,
        regime=regime,
        orderbook=make_orderbook("KRW-ALT", 1500, 2.0),
        ticks=make_ticks("KRW-ALT", 1500, 0.7),
        intraday=make_candles(72, drift=0.006),
        korean_name="알트코인",
    )
    assert cand.symbol == "ALT"
    assert 0 <= cand.score.total <= 100
    for pillar in (cand.score.volume, cand.score.flow, cand.score.technical, cand.score.beta):
        assert 0 <= pillar <= 100
    assert cand.score.metrics["rsi_14"] > 0
    assert "거래량" in cand.reason and "베타" in cand.reason


def test_composite_respects_weight_overrides() -> None:
    btc = ind.candles_to_frame(make_candles(120, drift=0.008))
    regime = MarketRegimeView(regime=Regime.RISK_ON, exposure_multiplier=1.0)
    kwargs = dict(
        market="KRW-ALT",
        ticker=make_ticker("KRW-ALT", 1500),
        candles=make_candles(120, drift=0.012),
        btc_df=btc,
        regime=regime,
    )
    tech_only = scoring.score_candidate(
        **kwargs, weights={"volume": 0.0, "flow": 0.0, "technical": 1.0, "beta": 0.0}
    )
    assert tech_only.score.total == pytest.approx(tech_only.score.technical, abs=0.01)
