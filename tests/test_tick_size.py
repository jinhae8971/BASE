from __future__ import annotations

import pytest

from broker.tick_size import (
    daily_limit_band,
    format_kis_price,
    snap_to_tick,
    tick_size,
)


@pytest.mark.parametrize(
    "price,expected",
    [
        (1_000, 1),
        (3_000, 5),
        (10_000, 10),
        (40_000, 50),
        (100_000, 100),
        (300_000, 500),
        (700_000, 1_000),
    ],
)
def test_tick_size_table(price: int, expected: int) -> None:
    assert tick_size(price) == expected


def test_snap_to_tick_directional() -> None:
    # 70_345 sits in the 50_000~200_000 band (tick=100)
    # down=70_300, up=70_400, nearest=70_300
    assert snap_to_tick(70_345, side="down") == 70_300
    assert snap_to_tick(70_345, side="up") == 70_400
    assert snap_to_tick(70_345, side="nearest") == 70_300


def test_snap_to_tick_already_aligned() -> None:
    # 70_300 already on a 100-tick boundary
    assert snap_to_tick(70_300, side="down") == 70_300
    assert snap_to_tick(70_300, side="up") == 70_300


def test_snap_to_tick_lower_band() -> None:
    # 25_000 sits in the 20_000~50_000 band (tick=50)
    assert snap_to_tick(25_175, side="down") == 25_150
    assert snap_to_tick(25_175, side="up") == 25_200


def test_format_kis_price_returns_string_int() -> None:
    s = format_kis_price(70_345.7, side="down")
    assert s.isdigit()
    assert int(s) == 70_300


def test_daily_limit_band() -> None:
    lower, upper = daily_limit_band(70_000)
    # ±30%, snapped: upper around 91_000, lower around 49_000
    assert 90_000 <= upper <= 91_500
    assert 49_000 <= lower <= 49_500
