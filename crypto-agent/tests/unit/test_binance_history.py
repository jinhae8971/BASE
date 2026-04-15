"""Historical kline fetcher + NDJSON archive round-trip."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.data.binance_history import (
    KLINES_LIMIT,
    _interval_ms,
    _parse_kline,
    fetch_history,
    load_archive,
    load_ndjson,
    save_ndjson,
)


def _kline_row(ts_ms: int, close: float) -> list:
    return [
        ts_ms, "100", "101", "99", str(close), "1000",
        ts_ms + 86_400_000,
        "100000", 42, "500", "50000", "0",
    ]


# ---------------------------------------------------------------------------
# _interval_ms
# ---------------------------------------------------------------------------


def test_interval_ms_covers_common_intervals() -> None:
    assert _interval_ms("1m") == 60_000
    assert _interval_ms("15m") == 900_000
    assert _interval_ms("1h") == 3_600_000
    assert _interval_ms("1d") == 86_400_000
    assert _interval_ms("1w") == 7 * 86_400_000


def test_interval_ms_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        _interval_ms("1y")


# ---------------------------------------------------------------------------
# parse_kline
# ---------------------------------------------------------------------------


def test_parse_kline_handles_string_numerics() -> None:
    row = _kline_row(1_700_000_000_000, 50_000.5)
    candle = _parse_kline(row)
    assert candle.close == 50_000.5
    assert candle.quote_volume == 100_000.0


# ---------------------------------------------------------------------------
# fetch_history
# ---------------------------------------------------------------------------


async def test_fetch_history_single_page() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v3/klines"
        start = datetime(2024, 1, 1, tzinfo=UTC)
        rows = [_kline_row(int(start.timestamp() * 1000) + i * 86_400_000, 100 + i) for i in range(5)]
        return httpx.Response(200, json=rows)

    client = httpx.AsyncClient(
        base_url="https://api.binance.com", transport=httpx.MockTransport(handler)
    )
    candles = await fetch_history(
        "BTCUSDT",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 1, 10, tzinfo=UTC),
        interval="1d",
        client=client,
    )
    assert len(candles) == 5
    assert candles[0].close == 100.0
    assert candles[-1].close == 104.0


async def test_fetch_history_paginates_when_first_page_is_full() -> None:
    """When Binance returns exactly KLINES_LIMIT rows, the fetcher should
    request another page starting one step after the last row."""
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        start = datetime(2024, 1, 1, tzinfo=UTC)
        start_ms = int(start.timestamp() * 1000)

        if len(calls) == 1:
            # First page: exactly the limit so the loop continues.
            rows = [
                _kline_row(start_ms + i * 86_400_000, 100 + i)
                for i in range(KLINES_LIMIT)
            ]
            return httpx.Response(200, json=rows)
        # Second page: two more rows, below the limit -> loop ends.
        offset = KLINES_LIMIT
        rows = [
            _kline_row(start_ms + (offset + i) * 86_400_000, 200 + i)
            for i in range(2)
        ]
        return httpx.Response(200, json=rows)

    client = httpx.AsyncClient(
        base_url="https://api.binance.com", transport=httpx.MockTransport(handler)
    )
    candles = await fetch_history(
        "BTCUSDT",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2030, 1, 1, tzinfo=UTC),
        interval="1d",
        client=client,
    )
    assert len(candles) == KLINES_LIMIT + 2
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# NDJSON persistence
# ---------------------------------------------------------------------------


def test_save_and_load_ndjson_round_trip(tmp_path: Path) -> None:
    from src.data.binance_md import Candle

    candles = [
        Candle(
            open_time=datetime(2024, 1, 1, tzinfo=UTC),
            open=100, high=110, low=90, close=105,
            volume=1_000, quote_volume=105_000,
        ),
        Candle(
            open_time=datetime(2024, 1, 2, tzinfo=UTC),
            open=105, high=115, low=100, close=112,
            volume=2_000, quote_volume=224_000,
        ),
    ]
    save_ndjson("BTCUSDT", candles, interval="1d", root=tmp_path)

    loaded = load_ndjson("BTCUSDT", interval="1d", root=tmp_path)
    assert len(loaded) == 2
    assert loaded[0].close == 105
    assert loaded[1].quote_volume == 224_000
    assert loaded[0].open_time == datetime(2024, 1, 1, tzinfo=UTC)


def test_load_archive_returns_empty_for_missing_symbol(tmp_path: Path) -> None:
    result = load_archive(["UNKNOWNUSDT"], interval="1d", root=tmp_path)
    assert result == {"UNKNOWNUSDT": []}
