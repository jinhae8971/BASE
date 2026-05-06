from __future__ import annotations

from data.macro import fetch_overnight_shock


def test_overnight_shock_returns_expected_keys() -> None:
    out = fetch_overnight_shock()
    assert "shock_pct" in out
    assert "breached" in out
    assert "threshold" in out
    assert "proxy" in out
    # threshold should be negative (below market)
    assert out["threshold"] < 0


def test_overnight_shock_no_yfinance_returns_zero(monkeypatch) -> None:
    """Force yfinance to fail — shock_pct should default to 0 (no breach)."""
    import builtins

    real_import = builtins.__import__

    def fail_import(name, *a, **kw):
        if name == "yfinance":
            raise ImportError("forced")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fail_import)
    out = fetch_overnight_shock()
    assert out["shock_pct"] == 0.0
    assert out["breached"] is False
