"""Universe index switcher — KOSPI200 / KRX300 / KOSDAQ150 / UNION."""
from __future__ import annotations

from data.universe import INDEX_CODES, get_universe


def _patch(monkeypatch, tmp_path, index_name: str = "KOSPI200") -> None:
    monkeypatch.setenv("MAIS_DATA_DIR", str(tmp_path))
    from common import config as c

    c.get_env.cache_clear()
    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {"universe": {"index": index_name}},
    )
    get_universe.cache_clear()


def test_index_codes_mapping_present() -> None:
    assert "KOSPI200" in INDEX_CODES
    assert "KRX300" in INDEX_CODES
    assert "KOSDAQ150" in INDEX_CODES
    assert "UNION" in INDEX_CODES
    # UNION should fan out to multiple codes
    assert len(INDEX_CODES["UNION"]) >= 2


def test_unknown_index_falls_back_to_kospi200(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path, "INVENTED_NAME")
    rows = get_universe()
    # Fallback list still works
    assert rows
    assert all("ticker" in r for r in rows)


def test_kospi200_default(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path, "KOSPI200")
    rows = get_universe()
    tickers = {r["ticker"] for r in rows}
    # Samsung must always be there in fallback
    assert "005930" in tickers


def test_kosdaq150_index_name_case_insensitive(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path, "kosdaq150")
    # Lower case in settings still works (uppercased in get_universe)
    rows = get_universe()
    assert rows  # falls back to FALLBACK_UNIVERSE without pykrx
