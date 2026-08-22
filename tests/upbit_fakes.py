"""Deterministic fakes for the Upbit subsystem tests — no network, ever."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

BASE_TIME = datetime(2026, 8, 1, 9, 0, 0)


def make_candles(
    n: int = 120,
    *,
    start: float = 1000.0,
    drift: float = 0.004,
    wobble: float = 0.02,
    base_value: float = 8_000_000_000.0,
    volume_spike_last: float = 1.0,
) -> list[dict[str, Any]]:
    """Newest-first daily candles, exactly as `/v1/candles/days` returns them."""
    rows: list[dict[str, Any]] = []
    price = start
    for i in range(n):
        price *= 1 + drift + wobble * math.sin(i / 4.0)
        high = price * 1.012
        low = price * 0.988
        value = base_value * (1 + 0.15 * math.sin(i / 6.0))
        if i == n - 1:
            value *= volume_spike_last
        rows.append(
            {
                "candle_date_time_kst": (BASE_TIME + timedelta(days=i)).isoformat(),
                "opening_price": price * 0.997,
                "high_price": high,
                "low_price": low,
                "trade_price": price,
                "candle_acc_trade_volume": value / price,
                "candle_acc_trade_price": value,
            }
        )
    return list(reversed(rows))


def make_ticker(market: str, price: float, turnover: float = 2.0e10) -> dict[str, Any]:
    return {
        "market": market,
        "trade_price": price,
        "signed_change_rate": 0.043,
        "acc_trade_price_24h": turnover,
        "acc_trade_volume_24h": turnover / price,
        "high_price": price * 1.05,
        "low_price": price * 0.95,
        "prev_closing_price": price * 0.96,
    }


def make_orderbook(market: str, price: float, bid_bias: float = 1.4) -> dict[str, Any]:
    units = [
        {
            "ask_price": price * (1 + 0.001 * (i + 1)),
            "bid_price": price * (1 - 0.001 * (i + 1)),
            "ask_size": 100.0,
            "bid_size": 100.0 * bid_bias,
        }
        for i in range(15)
    ]
    return {
        "market": market,
        "total_ask_size": sum(u["ask_size"] for u in units),
        "total_bid_size": sum(u["bid_size"] for u in units),
        "orderbook_units": units,
    }


def make_ticks(market: str, price: float, buy_ratio: float = 0.62, n: int = 200) -> list[dict]:
    return [
        {
            "market": market,
            "trade_price": price * (1 + 0.0001 * (i % 7 - 3)),
            "trade_volume": 1.0,
            "ask_bid": "BID" if (i / n) < buy_ratio else "ASK",
        }
        for i in range(n)
    ]


class FakeUpbitClient:
    """Stands in for :class:`upbit.client.UpbitClient` with scripted data.

    ``profiles`` maps a market to a dict of candle-generation kwargs so a test
    can make one coin obviously the best pick.
    """

    def __init__(
        self,
        markets: list[str],
        *,
        profiles: dict[str, dict[str, Any]] | None = None,
        prices: dict[str, float] | None = None,
        warnings: set[str] | None = None,
        cautions: dict[str, list[str]] | None = None,
        turnovers: dict[str, float] | None = None,
    ) -> None:
        self.markets = markets
        self.profiles = profiles or {}
        self.prices = prices or dict.fromkeys(markets, 1000.0)
        self.warnings = warnings or set()
        self.cautions = cautions or {}
        self.turnovers = turnovers or {}
        self.calls: list[tuple[str, str]] = []

    # -- quotation ------------------------------------------------------
    def get_markets(self, *, is_details: bool = True) -> list[dict[str, Any]]:
        return [
            {
                "market": m,
                "korean_name": m.split("-")[-1] + "코인",
                "english_name": m.split("-")[-1],
                "market_event": {
                    "warning": m in self.warnings,
                    "caution": {
                        key: key in self.cautions.get(m, [])
                        for key in (
                            "PRICE_FLUCTUATIONS",
                            "TRADING_VOLUME_SOARING",
                            "DEPOSIT_AMOUNT_SOARING",
                            "GLOBAL_PRICE_DIFFERENCES",
                            "CONCENTRATION_OF_SMALL_ACCOUNTS",
                        )
                    },
                },
            }
            for m in self.markets
        ]

    def get_tickers(self, markets: list[str]) -> list[dict[str, Any]]:
        self.calls.append(("ticker", ",".join(markets)))
        return [
            make_ticker(m, self.prices.get(m, 1000.0), self.turnovers.get(m, 2.0e10))
            for m in markets
            if m in self.prices or m in self.markets
        ]

    def get_day_candles(self, market: str, count: int = 200) -> list[dict[str, Any]]:
        self.calls.append(("day_candles", market))
        kwargs = dict(self.profiles.get(market, {}))
        kwargs.setdefault("start", self.prices.get(market, 1000.0) / 3)
        return make_candles(n=min(count, 120), **kwargs)

    def get_minute_candles(self, market: str, unit: int = 60, count: int = 200) -> list[dict]:
        self.calls.append(("minute_candles", market))
        return make_candles(n=min(count, 72), **self.profiles.get(market, {}))

    def get_orderbook(self, markets: list[str]) -> list[dict[str, Any]]:
        self.calls.append(("orderbook", ",".join(markets)))
        return [make_orderbook(m, self.prices.get(m, 1000.0)) for m in markets]

    def get_trade_ticks(self, market: str, count: int = 200) -> list[dict[str, Any]]:
        self.calls.append(("ticks", market))
        return make_ticks(market, self.prices.get(market, 1000.0), n=min(count, 200))

    # -- exchange -------------------------------------------------------
    def has_credentials(self) -> bool:
        return False

    def get_accounts(self) -> list[dict[str, Any]]:
        raise AssertionError("tests must never hit the private Upbit API")

    def close(self) -> None:
        pass
