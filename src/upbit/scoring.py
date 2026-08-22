"""Composite day-trading score: 거래량 · 수급 · 차트 · 베타.

Four pillars, each normalised to 0-100 and then blended with the weights in
``upbit.scoring.weights``:

``volume``     거래량/거래대금 급증 — is money arriving *today* vs. its own norm?
``flow``       수급 — orderbook imbalance, taker aggression, money-flow trend.
``technical``  차트 — trend alignment, momentum, breakout posture, tradable ATR.
``beta``       알트 베타 — leverage to a BTC-led move plus relative strength.

The beta pillar is what turns a rising market into *alt* upside: in a risk-on
BTC regime we deliberately reward high-beta names that are already outrunning
BTC, and in risk-off the regime gate below shuts entries off entirely.

Every intermediate metric is kept in :attr:`ScoreBreakdown.metrics` so the
dashboard's 분석내역 tab can explain each pick after the fact.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from common.config import get_setting
from common.logging import get_logger

from . import indicators as ind
from .types import Candidate, MarketRegimeView, Regime, ScoreBreakdown

log = get_logger(__name__)

DEFAULT_WEIGHTS = {"volume": 0.30, "flow": 0.25, "technical": 0.30, "beta": 0.15}


# ----------------------------------------------------------------------
# Normalisation helpers
# ----------------------------------------------------------------------
def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return float(min(max(value, low), high))


def _ramp(value: float, low: float, high: float) -> float:
    """Linear 0→100 ramp between ``low`` and ``high`` (saturating outside)."""
    if high <= low:
        return 50.0
    return _clamp((value - low) / (high - low) * 100.0)


def _band(value: float, lo: float, best_lo: float, best_hi: float, hi: float) -> float:
    """Trapezoid: 100 inside ``[best_lo, best_hi]``, tapering to 0 at the edges.

    Used where *more* stops being *better* — RSI that is already exhausted, or
    volatility so high the stop gets taken out by noise.
    """
    if value <= lo or value >= hi:
        return 0.0
    if best_lo <= value <= best_hi:
        return 100.0
    if value < best_lo:
        return _clamp((value - lo) / (best_lo - lo) * 100.0)
    return _clamp((hi - value) / (hi - best_hi) * 100.0)


def _safe(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(out):
        return default
    return out


# ----------------------------------------------------------------------
# Market regime (BTC)
# ----------------------------------------------------------------------
def assess_regime(btc_candles: list[dict[str, Any]]) -> MarketRegimeView:
    """Read the BTC tape; this gates how much risk the engine may take today."""
    df = ind.candles_to_frame(btc_candles)
    if len(df) < 25:
        return MarketRegimeView(
            regime=Regime.NEUTRAL, exposure_multiplier=0.6, rationale="BTC 캔들 데이터 부족"
        )

    close = df["close"]
    price = float(close.iloc[-1])
    ma20 = float(ind.sma(close, 20).iloc[-1])
    ma50 = float(ind.sma(close, 50).iloc[-1]) if len(df) >= 50 else ma20
    btc_rsi = float(ind.rsi(close, 14).iloc[-1])
    ret_7d = float(close.iloc[-1] / close.iloc[-8] - 1) if len(close) >= 8 else 0.0

    above20 = price > ma20
    above50 = price > ma50
    score = sum((above20, above50, btc_rsi >= 50, ret_7d > 0))

    if score >= 3 and above20:
        regime, reason = Regime.RISK_ON, "BTC가 20/50일선 위, 모멘텀 양호 — 알트 베타 확대 국면"
    elif score <= 1:
        regime, reason = Regime.RISK_OFF, "BTC 추세 이탈 — 신규 진입 중단"
    else:
        regime, reason = Regime.NEUTRAL, "BTC 방향성 혼조 — 노출 축소"

    exposure = _safe(
        get_setting(f"upbit.risk.regime_exposure.{regime.value}"),
        {"risk_on": 1.0, "neutral": 0.6, "risk_off": 0.0}[regime.value],
    )
    return MarketRegimeView(
        regime=regime,
        exposure_multiplier=exposure,
        btc_price=price,
        btc_return_7d=ret_7d,
        btc_rsi=btc_rsi,
        above_ma20=above20,
        above_ma50=above50,
        rationale=reason,
    )


# ----------------------------------------------------------------------
# Pillar 1 — 거래량
# ----------------------------------------------------------------------
def score_volume(df: pd.DataFrame, ticker: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """Reward turnover that is spiking against the coin's own 20-day baseline."""
    metrics: dict[str, float] = {}
    if len(df) < 10 or "value" not in df:
        return 0.0, metrics

    value = df["value"]
    today_value = _safe(ticker.get("acc_trade_price_24h"), _safe(value.iloc[-1]))
    avg20 = _safe(value.iloc[-21:-1].mean()) if len(value) > 21 else _safe(value.mean())
    avg5 = _safe(value.iloc[-6:-1].mean()) if len(value) > 6 else avg20

    surge = today_value / avg20 if avg20 > 0 else 0.0
    trend = avg5 / avg20 if avg20 > 0 else 0.0
    vol_z = 0.0
    if len(value) > 21:
        tail = value.iloc[-21:-1]
        sd = _safe(tail.std(ddof=0))
        if sd > 0:
            vol_z = (today_value - _safe(tail.mean())) / sd

    metrics.update(
        {
            "vol_surge_ratio": round(surge, 3),
            "vol_trend_5d_20d": round(trend, 3),
            "vol_zscore": round(vol_z, 3),
            "turnover_24h_krw": round(today_value, 0),
            "turnover_avg20_krw": round(avg20, 0),
        }
    )

    # A 2x day is already interesting; 5x is the top of the scale.
    s_surge = _ramp(surge, 0.8, 5.0)
    s_trend = _ramp(trend, 0.7, 2.5)
    s_z = _ramp(vol_z, -0.5, 4.0)
    s_liquidity = _ramp(np.log10(max(today_value, 1.0)), 9.0, 11.5)  # 1e9 → 3e11 KRW

    score = 0.40 * s_surge + 0.20 * s_trend + 0.20 * s_z + 0.20 * s_liquidity
    return _clamp(score), metrics


# ----------------------------------------------------------------------
# Pillar 2 — 수급
# ----------------------------------------------------------------------
def score_flow(
    df: pd.DataFrame,
    orderbook: dict[str, Any] | None,
    ticks: list[dict[str, Any]] | None,
) -> tuple[float, dict[str, float]]:
    """Blend resting-bid depth, taker aggression and multi-day money flow."""
    metrics: dict[str, float] = {}
    parts: list[tuple[float, float]] = []  # (weight, score)

    if orderbook:
        bid = _safe(orderbook.get("total_bid_size"))
        ask = _safe(orderbook.get("total_ask_size"))
        denom = bid + ask
        imbalance = (bid - ask) / denom if denom > 0 else 0.0
        units = orderbook.get("orderbook_units") or []
        near_bid = sum(_safe(u.get("bid_size")) * _safe(u.get("bid_price")) for u in units[:5])
        near_ask = sum(_safe(u.get("ask_size")) * _safe(u.get("ask_price")) for u in units[:5])
        near_denom = near_bid + near_ask
        near_imbalance = (near_bid - near_ask) / near_denom if near_denom > 0 else 0.0
        metrics["orderbook_imbalance"] = round(imbalance, 4)
        metrics["orderbook_imbalance_top5"] = round(near_imbalance, 4)
        parts.append((0.25, _ramp(imbalance, -0.5, 0.5)))
        parts.append((0.20, _ramp(near_imbalance, -0.5, 0.5)))

    if ticks:
        buy_vol = sum(_safe(t.get("trade_volume")) for t in ticks if t.get("ask_bid") == "BID")
        total_vol = sum(_safe(t.get("trade_volume")) for t in ticks)
        taker_ratio = buy_vol / total_vol if total_vol > 0 else 0.5
        buy_krw = sum(
            _safe(t.get("trade_volume")) * _safe(t.get("trade_price"))
            for t in ticks
            if t.get("ask_bid") == "BID"
        )
        total_krw = sum(_safe(t.get("trade_volume")) * _safe(t.get("trade_price")) for t in ticks)
        taker_krw_ratio = buy_krw / total_krw if total_krw > 0 else 0.5
        metrics["taker_buy_ratio"] = round(taker_ratio, 4)
        metrics["taker_buy_krw_ratio"] = round(taker_krw_ratio, 4)
        parts.append((0.25, _ramp(taker_ratio, 0.35, 0.68)))
        parts.append((0.10, _ramp(taker_krw_ratio, 0.35, 0.68)))

    if len(df) >= 20:
        mfi = _safe(ind.money_flow_index(df, 14).iloc[-1], 50.0)
        obv_slope = ind.slope(ind.obv(df), 10)
        metrics["mfi_14"] = round(mfi, 2)
        metrics["obv_slope_10d"] = round(obv_slope, 5)
        # MFI above ~80 is distribution, not accumulation.
        parts.append((0.12, _band(mfi, 20, 55, 80, 95)))
        parts.append((0.08, _ramp(obv_slope, -0.02, 0.08)))

    if not parts:
        return 0.0, metrics
    total_weight = sum(w for w, _ in parts)
    return _clamp(sum(w * s for w, s in parts) / total_weight), metrics


# ----------------------------------------------------------------------
# Pillar 3 — 차트
# ----------------------------------------------------------------------
def score_technical(
    df: pd.DataFrame, intraday: pd.DataFrame | None = None
) -> tuple[float, dict[str, float]]:
    """Trend alignment + momentum + breakout posture + a tradable ATR band."""
    metrics: dict[str, float] = {}
    if len(df) < 25:
        return 0.0, metrics

    close = df["close"]
    price = _safe(close.iloc[-1])
    ma5 = _safe(ind.sma(close, 5).iloc[-1], price)
    ma20 = _safe(ind.sma(close, 20).iloc[-1], price)
    ma60 = _safe(ind.sma(close, 60).iloc[-1], ma20) if len(df) >= 60 else ma20

    # --- trend: how many of the stack conditions hold
    conds = [price > ma5, ma5 > ma20, ma20 > ma60, price > ma20]
    trend_score = 100.0 * sum(conds) / len(conds)

    # --- momentum
    coin_rsi = _safe(ind.rsi(close, 14).iloc[-1], 50.0)
    _, _, hist = ind.macd(close)
    hist_now = _safe(hist.iloc[-1])
    hist_prev = _safe(hist.iloc[-2]) if len(hist) > 2 else hist_now
    hist_norm = hist_now / price if price > 0 else 0.0
    # 52-70 is the momentum sweet spot; >85 is chasing an exhausted move.
    momentum_score = (
        0.5 * _band(coin_rsi, 35, 52, 72, 88)
        + 0.3 * _ramp(hist_norm, -0.005, 0.02)
        + 0.2 * (100.0 if hist_now > hist_prev else 0.0)
    )

    # --- breakout posture
    high20 = _safe(df["high"].iloc[-20:].max(), price)
    low20 = _safe(df["low"].iloc[-20:].min(), price)
    rng = high20 - low20
    range_pos = (price - low20) / rng if rng > 0 else 0.5
    dist_to_high = (high20 - price) / price if price > 0 else 0.0
    pct_b = _safe(ind.percent_b(close).iloc[-1], 0.5)
    breakout_score = (
        0.4 * _ramp(range_pos, 0.35, 0.95)
        + 0.3 * _band(dist_to_high, -0.01, 0.0, 0.06, 0.25)
        + 0.3 * _band(pct_b, 0.2, 0.6, 1.0, 1.35)
    )

    # --- volatility fit: we need movement, but not stop-gap-eating chaos
    atr_val = _safe(ind.atr(df, 14).iloc[-1])
    atr_pct = atr_val / price if price > 0 else 0.0
    vol_score = _band(atr_pct, 0.005, 0.025, 0.09, 0.20)

    # --- intraday confirmation (optional, from 60m candles)
    intraday_score = 50.0
    if intraday is not None and len(intraday) >= 20:
        ic = intraday["close"]
        i_ma = _safe(ind.sma(ic, 20).iloc[-1], _safe(ic.iloc[-1]))
        i_price = _safe(ic.iloc[-1])
        i_rsi = _safe(ind.rsi(ic, 14).iloc[-1], 50.0)
        intraday_score = 0.5 * (100.0 if i_price > i_ma else 0.0) + 0.5 * _band(
            i_rsi, 30, 50, 72, 90
        )
        metrics["intraday_rsi"] = round(i_rsi, 2)
        metrics["intraday_above_ma20"] = float(i_price > i_ma)

    metrics.update(
        {
            "price": round(price, 6),
            "ma5": round(ma5, 6),
            "ma20": round(ma20, 6),
            "ma60": round(ma60, 6),
            "rsi_14": round(coin_rsi, 2),
            "macd_hist": round(hist_now, 6),
            "range_position_20d": round(range_pos, 4),
            "dist_to_high20": round(dist_to_high, 4),
            "percent_b": round(pct_b, 4),
            "atr_pct": round(atr_pct, 4),
            "trend_score": round(trend_score, 1),
            "momentum_score": round(momentum_score, 1),
            "breakout_score": round(breakout_score, 1),
            "volatility_score": round(vol_score, 1),
            "intraday_score": round(intraday_score, 1),
        }
    )

    score = (
        0.30 * trend_score
        + 0.28 * momentum_score
        + 0.24 * breakout_score
        + 0.10 * vol_score
        + 0.08 * intraday_score
    )
    return _clamp(score), metrics


# ----------------------------------------------------------------------
# Pillar 4 — 알트 베타 / 상대강도
# ----------------------------------------------------------------------
def score_beta(
    df: pd.DataFrame, btc_df: pd.DataFrame, regime: MarketRegimeView
) -> tuple[float, dict[str, float]]:
    """High beta is an *asset* in a BTC-led rally and a liability otherwise."""
    metrics: dict[str, float] = {}
    if len(df) < 15 or len(btc_df) < 15:
        return 50.0, metrics

    coin_ret = df["close"].pct_change()
    btc_ret = btc_df["close"].pct_change()
    beta = ind.beta_against(coin_ret, btc_ret, window=30)

    ret_7d = _safe(df["close"].iloc[-1] / df["close"].iloc[-8] - 1) if len(df) >= 8 else 0.0
    btc_7d = (
        _safe(btc_df["close"].iloc[-1] / btc_df["close"].iloc[-8] - 1) if len(btc_df) >= 8 else 0.0
    )
    rel_strength = ret_7d - btc_7d

    ret_30d = _safe(df["close"].iloc[-1] / df["close"].iloc[-31] - 1) if len(df) >= 31 else 0.0
    vol = ind.realized_vol(df["close"], 20)

    metrics.update(
        {
            "beta_vs_btc_30d": round(beta, 3),
            "return_7d": round(ret_7d, 4),
            "btc_return_7d": round(btc_7d, 4),
            "relative_strength_7d": round(rel_strength, 4),
            "return_30d": round(ret_30d, 4),
            "realized_vol_20d": round(vol, 4),
        }
    )

    # β 1.2-2.5 is the pump-leverage sweet spot; past ~3.5 it is mostly noise.
    beta_fit = _band(beta, 0.3, 1.2, 2.5, 4.0)
    rs_score = _ramp(rel_strength, -0.08, 0.20)
    persistence = _band(ret_30d, -0.40, 0.05, 0.80, 3.00)

    if regime.regime is Regime.RISK_ON:
        weights = (0.45, 0.35, 0.20)
    elif regime.regime is Regime.NEUTRAL:
        weights = (0.25, 0.50, 0.25)
    else:
        # Risk-off: prefer low beta. Entries are gated off anyway, but the score
        # still has to rank honestly for the analysis log.
        beta_fit = _band(beta, 0.0, 0.2, 1.0, 2.0)
        weights = (0.50, 0.35, 0.15)

    score = weights[0] * beta_fit + weights[1] * rs_score + weights[2] * persistence
    return _clamp(score), metrics


# ----------------------------------------------------------------------
# Composite
# ----------------------------------------------------------------------
def get_weights() -> dict[str, float]:
    raw = get_setting("upbit.scoring.weights", DEFAULT_WEIGHTS) or DEFAULT_WEIGHTS
    weights = {k: _safe(raw.get(k), DEFAULT_WEIGHTS[k]) for k in DEFAULT_WEIGHTS}
    total = sum(weights.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: v / total for k, v in weights.items()}


def score_candidate(
    *,
    market: str,
    ticker: dict[str, Any],
    candles: list[dict[str, Any]],
    btc_df: pd.DataFrame,
    regime: MarketRegimeView,
    orderbook: dict[str, Any] | None = None,
    ticks: list[dict[str, Any]] | None = None,
    intraday: list[dict[str, Any]] | None = None,
    korean_name: str | None = None,
    weights: dict[str, float] | None = None,
) -> Candidate:
    """Score one market and return a fully-populated :class:`Candidate`."""
    df = ind.candles_to_frame(candles)
    intraday_df = ind.candles_to_frame(intraday) if intraday else None
    w = weights or get_weights()

    vol_score, vol_metrics = score_volume(df, ticker)
    flow_score, flow_metrics = score_flow(df, orderbook, ticks)
    tech_score, tech_metrics = score_technical(df, intraday_df)
    beta_score, beta_metrics = score_beta(df, btc_df, regime)

    total = (
        w["volume"] * vol_score
        + w["flow"] * flow_score
        + w["technical"] * tech_score
        + w["beta"] * beta_score
    )

    breakdown = ScoreBreakdown(
        volume=round(vol_score, 2),
        flow=round(flow_score, 2),
        technical=round(tech_score, 2),
        beta=round(beta_score, 2),
        total=round(_clamp(total), 2),
        metrics={**vol_metrics, **flow_metrics, **tech_metrics, **beta_metrics},
    )
    return Candidate(
        market=market,
        symbol=market.split("-")[-1],
        korean_name=korean_name,
        price=_safe(ticker.get("trade_price")),
        change_rate_24h=_safe(ticker.get("signed_change_rate")),
        trade_price_24h=_safe(ticker.get("acc_trade_price_24h")),
        score=breakdown,
        reason=explain(breakdown),
    )


def explain(score: ScoreBreakdown) -> str:
    """One-line Korean rationale for the 분석내역 table."""
    m = score.metrics
    bits = [
        f"거래량 {score.volume:.0f}(급증 {m.get('vol_surge_ratio', 0):.1f}x)",
        f"수급 {score.flow:.0f}(체결강도 {m.get('taker_buy_ratio', 0.5) * 100:.0f}%)",
        f"차트 {score.technical:.0f}(RSI {m.get('rsi_14', 0):.0f}, 20일 위치 "
        f"{m.get('range_position_20d', 0) * 100:.0f}%)",
        f"베타 {score.beta:.0f}(β {m.get('beta_vs_btc_30d', 1):.2f}, BTC대비 "
        f"{m.get('relative_strength_7d', 0) * 100:+.1f}%p)",
    ]
    return " · ".join(bits)
