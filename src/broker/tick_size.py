"""KRX 호가 단위 (tick size) snapping.

KRX는 가격대별로 호가단위가 다릅니다 (코스피·코스닥 통일, 2023.01 개편 후):

    가격 < 2,000          → 1원
    < 5,000               → 5원
    < 20,000              → 10원
    < 50,000              → 50원
    < 200,000             → 100원
    < 500,000             → 500원
    >= 500,000            → 1,000원

지정가 주문이 호가단위에 맞지 않으면 KIS API가 거부합니다. 모든 매매 가격은
``snap_to_tick`` 또는 ``format_kis_price``를 거쳐야 합니다.
"""
from __future__ import annotations

import math

# (상한 가격, 호가단위) — 가격이 상한 미만이면 해당 단위 적용
_TICK_TABLE: tuple[tuple[float, int], ...] = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (float("inf"), 1_000),
)


def tick_size(price: float) -> int:
    for upper, tick in _TICK_TABLE:
        if price < upper:
            return tick
    return 1_000


def snap_to_tick(price: float, *, side: str = "down") -> int:
    """호가단위에 맞게 정수 가격으로 스냅.

    side='down' → 매수에 안전 (한 틱 아래로)
    side='up'   → 매도에 안전 (한 틱 위로)
    side='nearest' → 반올림
    """
    if price <= 0:
        return 0
    t = tick_size(price)
    if side == "up":
        return int(math.ceil(price / t) * t)
    if side == "nearest":
        return int(round(price / t) * t)
    return int(math.floor(price / t) * t)


def format_kis_price(price: float, *, side: str = "down") -> str:
    """KIS ORD_UNPR에 들어갈 문자열 (호가단위 스냅된 정수)."""
    return str(snap_to_tick(price, side=side))


# Daily price limit for KOSPI/KOSDAQ (±30% standard). For PA, returns the
# allowed range so we never submit orders outside the band.
DAILY_LIMIT_PCT = 0.30


def daily_limit_band(prev_close: float) -> tuple[int, int]:
    """전일 종가 기준 ±30% 상하한가 밴드 (호가단위 스냅 적용)."""
    if prev_close <= 0:
        return 0, 0
    upper = snap_to_tick(prev_close * (1 + DAILY_LIMIT_PCT), side="down")
    lower = snap_to_tick(prev_close * (1 - DAILY_LIMIT_PCT), side="up")
    return lower, upper
