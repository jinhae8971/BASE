"""Universe screening — including the 주의-flag nuance that matters most here."""
from __future__ import annotations

from upbit_fakes import FakeUpbitClient

from upbit.holdings import HoldingsGuard
from upbit.strategy import UniverseConfig
from upbit.types import LongTermHolding
from upbit.universe import UniverseBuilder, market_flags

MARKETS = ["KRW-BTC", "KRW-XRP", "KRW-DOGE", "KRW-SOL", "KRW-USDT", "BTC-XRP"]


def build(client: FakeUpbitClient, guard: HoldingsGuard | None = None, **cfg) -> UniverseBuilder:
    return UniverseBuilder(client, UniverseConfig(**cfg), guard or HoldingsGuard([]))


def test_market_flags_reads_the_typed_caution_map() -> None:
    warning, cautions = market_flags(
        {
            "market": "KRW-X",
            "market_event": {
                "warning": False,
                "caution": {"TRADING_VOLUME_SOARING": True, "PRICE_FLUCTUATIONS": False},
            },
        }
    )
    assert warning is False
    assert cautions == {"TRADING_VOLUME_SOARING"}


def test_market_flags_handles_the_legacy_shape() -> None:
    assert market_flags({"market": "KRW-X", "market_warning": "CAUTION"}) == (False, {"CAUTION"})
    warning, _ = market_flags({"market": "KRW-X", "market_warning": "WARNING"})
    assert warning is True
    assert market_flags({"market": "KRW-X", "market_warning": "NONE"}) == (False, set())


def test_only_krw_markets_are_considered() -> None:
    client = FakeUpbitClient(MARKETS)
    markets, names, _ = build(client).eligible_markets()
    assert "BTC-XRP" not in markets
    assert all(m.startswith("KRW-") for m in markets)
    assert names["KRW-XRP"]


def test_volume_soaring_alone_does_not_disqualify() -> None:
    """A turnover spike is the entry signal — it must not screen a coin out."""
    client = FakeUpbitClient(MARKETS, cautions={"KRW-DOGE": ["TRADING_VOLUME_SOARING"]})
    markets, _, rejected = build(client).eligible_markets()
    assert "KRW-DOGE" in markets
    assert not any(r["market"] == "KRW-DOGE" for r in rejected)


def test_risky_caution_types_do_disqualify() -> None:
    client = FakeUpbitClient(
        MARKETS,
        cautions={
            "KRW-DOGE": ["PRICE_FLUCTUATIONS"],
            "KRW-SOL": ["CONCENTRATION_OF_SMALL_ACCOUNTS", "TRADING_VOLUME_SOARING"],
        },
    )
    markets, _, rejected = build(client).eligible_markets()
    assert "KRW-DOGE" not in markets and "KRW-SOL" not in markets
    reasons = {r["market"]: r["reason"] for r in rejected}
    assert "PRICE_FLUCTUATIONS" in reasons["KRW-DOGE"]
    assert "CONCENTRATION_OF_SMALL_ACCOUNTS" in reasons["KRW-SOL"]


def test_caution_types_are_configurable() -> None:
    client = FakeUpbitClient(MARKETS, cautions={"KRW-DOGE": ["TRADING_VOLUME_SOARING"]})
    markets, _, _ = build(client, caution_types=["TRADING_VOLUME_SOARING"]).eligible_markets()
    assert "KRW-DOGE" not in markets


def test_exclude_caution_toggle_overrides_the_type_list() -> None:
    client = FakeUpbitClient(MARKETS, cautions={"KRW-DOGE": ["PRICE_FLUCTUATIONS"]})
    markets, _, _ = build(client, exclude_caution=False).eligible_markets()
    assert "KRW-DOGE" in markets


def test_warning_markets_and_stablecoins_are_dropped() -> None:
    client = FakeUpbitClient(MARKETS, warnings={"KRW-XRP"})
    markets, _, rejected = build(client).eligible_markets()
    reasons = {r["market"]: r["reason"] for r in rejected}
    assert reasons["KRW-XRP"] == "유의 종목"
    assert reasons["KRW-USDT"] == "스테이블코인"
    assert markets == ["KRW-BTC", "KRW-DOGE", "KRW-SOL"]


def test_long_term_and_blacklist_are_dropped() -> None:
    client = FakeUpbitClient(MARKETS)
    guard = HoldingsGuard([LongTermHolding(symbol="BTC")])
    markets, _, rejected = build(client, guard, manual_blacklist=["doge"]).eligible_markets()
    reasons = {r["market"]: r["reason"] for r in rejected}
    assert "장기보유" in reasons["KRW-BTC"]
    assert reasons["KRW-DOGE"] == "수동 제외 목록"
    assert markets == ["KRW-XRP", "KRW-SOL"]   # USDT is dropped as a stablecoin


def test_liquidity_floor_and_ranking_cap() -> None:
    universe = [f"KRW-C{i}" for i in range(7)]
    turnovers = {m: (7 - i) * 1e10 for i, m in enumerate(universe)}
    turnovers["KRW-C6"] = 1e8          # below the floor
    client = FakeUpbitClient(
        universe, prices=dict.fromkeys(universe, 1000.0), turnovers=turnovers
    )
    kept, rejected = build(
        client, min_trade_price_24h=1e10, max_candidates=5
    ).liquid_candidates(universe)

    assert [t["market"] for t in kept] == [f"KRW-C{i}" for i in range(5)]  # by turnover desc
    reasons = {r["market"]: r["reason"] for r in rejected}
    assert "거래대금 미달" in reasons["KRW-C6"]
    assert "순위" in reasons["KRW-C5"]


def test_build_reports_both_screens() -> None:
    client = FakeUpbitClient(MARKETS, turnovers=dict.fromkeys(MARKETS, 5e10))
    result = build(client, min_trade_price_24h=1e10).build()
    assert result["candidates"] > 0
    assert result["screened"] >= result["candidates"]
    assert isinstance(result["rejected"], list)
    assert set(result["korean_names"])


def test_empty_market_list_is_safe() -> None:
    assert build(FakeUpbitClient([])).liquid_candidates([]) == ([], [])
