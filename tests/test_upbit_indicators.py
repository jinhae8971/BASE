"""Indicator maths — the layer every score is built on."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from upbit_fakes import make_candles

from upbit import indicators as ind


@pytest.fixture
def df() -> pd.DataFrame:
    return ind.candles_to_frame(make_candles(120))


def test_candles_to_frame_flips_to_chronological(df: pd.DataFrame) -> None:
    raw = make_candles(30)
    frame = ind.candles_to_frame(raw)
    assert len(frame) == 30
    # Upbit ships newest-first; the frame must end on the newest bar.
    assert frame["close"].iloc[-1] == pytest.approx(raw[0]["trade_price"])
    assert frame["close"].iloc[0] == pytest.approx(raw[-1]["trade_price"])
    assert set(["open", "high", "low", "close", "volume", "value"]) <= set(frame.columns)


def test_candles_to_frame_handles_empty() -> None:
    frame = ind.candles_to_frame([])
    assert frame.empty
    assert "close" in frame.columns


def test_rsi_bounds_and_monotone_series() -> None:
    rising = pd.Series(np.arange(1, 80, dtype=float))
    rsi = ind.rsi(rising, 14)
    assert rsi.iloc[-1] == pytest.approx(100.0)
    assert rsi.between(0, 100).all()

    falling = pd.Series(np.arange(80, 1, -1, dtype=float))
    assert ind.rsi(falling, 14).iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_macd_histogram_is_line_minus_signal(df: pd.DataFrame) -> None:
    line, signal, hist = ind.macd(df["close"])
    assert hist.dropna().equals((line - signal).dropna())


def test_percent_b_is_centred_for_flat_series() -> None:
    flat = pd.Series([100.0] * 40)
    assert ind.percent_b(flat).iloc[-1] == pytest.approx(0.5)


def test_atr_is_positive(df: pd.DataFrame) -> None:
    assert ind.atr(df, 14).iloc[-1] > 0


def test_beta_against_self_is_one() -> None:
    rets = pd.Series(np.random.default_rng(7).normal(0, 0.02, 60))
    assert ind.beta_against(rets, rets, 30) == pytest.approx(1.0, abs=1e-9)


def test_beta_scales_with_amplitude() -> None:
    base = pd.Series(np.random.default_rng(11).normal(0, 0.02, 60))
    assert ind.beta_against(base * 2, base, 30) == pytest.approx(2.0, abs=1e-9)


def test_beta_defaults_to_one_without_enough_history() -> None:
    short = pd.Series([0.01, -0.02, 0.03])
    assert ind.beta_against(short, short, 30) == 1.0


def test_money_flow_index_bounds(df: pd.DataFrame) -> None:
    mfi = ind.money_flow_index(df, 14)
    assert mfi.between(0, 100).all()


def test_max_drawdown_of_monotone_rise_is_zero() -> None:
    assert ind.max_drawdown(pd.Series([1.0, 2.0, 3.0, 4.0])) == pytest.approx(0.0)


def test_max_drawdown_captures_the_trough() -> None:
    assert ind.max_drawdown(pd.Series([100.0, 120.0, 60.0, 90.0])) == pytest.approx(-0.5)


def test_slope_sign_follows_direction() -> None:
    assert ind.slope(pd.Series(np.arange(20, dtype=float) + 10), 10) > 0
    assert ind.slope(pd.Series(np.arange(20, 0, -1, dtype=float)), 10) < 0
