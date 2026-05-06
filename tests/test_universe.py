from __future__ import annotations

from data.universe import FALLBACK_UNIVERSE, get_universe, sector_map


def test_universe_returns_nonempty() -> None:
    rows = get_universe()
    assert rows, "universe should always return at least the fallback list"
    assert all("ticker" in r and len(r["ticker"]) == 6 for r in rows)


def test_sector_map_covers_universe() -> None:
    smap = sector_map()
    rows = get_universe()
    for r in rows:
        assert r["ticker"] in smap


def test_fallback_universe_has_kospi_megacaps() -> None:
    tickers = {r["ticker"] for r in FALLBACK_UNIVERSE}
    # Samsung Electronics, SK Hynix, Hyundai Motor — must be present
    assert {"005930", "000660", "005380"} <= tickers
