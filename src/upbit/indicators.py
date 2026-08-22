"""Technical indicators over Upbit candle series.

Everything here is pure numpy/pandas and takes *chronological* (oldest-first)
series — :func:`candles_to_frame` handles the flip, since Upbit returns candles
newest-first.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

CANDLE_COLUMNS = {
    "opening_price": "open",
    "high_price": "high",
    "low_price": "low",
    "trade_price": "close",
    "candle_acc_trade_volume": "volume",
    "candle_acc_trade_price": "value",
}


def candles_to_frame(candles: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalise raw Upbit candles into an oldest-first OHLCV frame."""
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "value"])

    df = pd.DataFrame(candles)
    ts_col = next(
        (c for c in ("candle_date_time_kst", "candle_date_time_utc") if c in df.columns), None
    )
    df = df.rename(columns=CANDLE_COLUMNS)
    keep = [c for c in ("open", "high", "low", "close", "volume", "value") if c in df.columns]
    out = df[keep].astype(float)
    if ts_col:
        out.index = pd.to_datetime(df[ts_col])
    # Upbit ships newest-first; indicators assume the opposite.
    return out.iloc[::-1].reset_index(drop=ts_col is None)


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=max(2, window // 2)).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=max(2, span // 2)).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    # A flat-or-rising series has no losses; RSI is 100 there, not NaN.
    return out.where(avg_loss.ne(0.0) | avg_gain.eq(0.0), 100.0).fillna(50.0)


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return ``(macd_line, signal_line, histogram)``."""
    line = ema(series, fast) - ema(series, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(
    series: pd.Series, window: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return ``(upper, middle, lower)``."""
    mid = sma(series, window)
    sd = series.rolling(window, min_periods=max(2, window // 2)).std(ddof=0)
    return mid + num_std * sd, mid, mid - num_std * sd


def percent_b(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
    """Position inside the Bollinger band: 0 = lower rail, 1 = upper rail."""
    upper, _, lower = bollinger(series, window, num_std)
    width = (upper - lower).replace(0.0, np.nan)
    return ((series - lower) / width).fillna(0.5)


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range in price units."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window // 2).mean()


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume — cumulative signed volume."""
    direction = np.sign(df["close"].diff().fillna(0.0))
    return (direction * df["volume"]).cumsum()


def money_flow_index(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """MFI — a volume-weighted RSI, our proxy for 수급 pressure on daily bars."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    flow = typical * df["volume"]
    delta = typical.diff()
    pos = flow.where(delta > 0, 0.0).rolling(window, min_periods=window // 2).sum()
    neg = flow.where(delta < 0, 0.0).rolling(window, min_periods=window // 2).sum()
    ratio = pos / neg.replace(0.0, np.nan)
    return (100 - 100 / (1 + ratio)).fillna(50.0)


def slope(series: pd.Series, window: int = 10) -> float:
    """Least-squares slope of the last ``window`` points, normalised by level."""
    tail = series.dropna().tail(window)
    if len(tail) < 3:
        return 0.0
    x = np.arange(len(tail), dtype=float)
    coef = np.polyfit(x, tail.to_numpy(dtype=float), 1)[0]
    level = float(np.abs(tail.mean()))
    return float(coef / level) if level > 0 else 0.0


def realized_vol(series: pd.Series, window: int = 20) -> float:
    """Annualised stdev of daily log returns (crypto trades 365 days)."""
    rets = np.log(series / series.shift(1)).dropna().tail(window)
    if len(rets) < 3:
        return 0.0
    return float(rets.std(ddof=0) * np.sqrt(365))


def beta_against(returns: pd.Series, benchmark: pd.Series, window: int = 30) -> float:
    """Rolling beta of an alt against BTC — the pump-leverage estimate."""
    joined = pd.concat([returns, benchmark], axis=1, join="inner").dropna().tail(window)
    if len(joined) < 10:
        return 1.0
    a = joined.iloc[:, 0].to_numpy(dtype=float)
    b = joined.iloc[:, 1].to_numpy(dtype=float)
    var = float(np.var(b, ddof=0))
    if var <= 0:
        return 1.0
    return float(np.cov(a, b, ddof=0)[0, 1] / var)


def drawdown_series(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    return (equity - peak) / peak.replace(0.0, np.nan)


def max_drawdown(equity: pd.Series) -> float:
    dd = drawdown_series(equity).dropna()
    return float(dd.min()) if len(dd) else 0.0
