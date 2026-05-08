"""UX helpers — friendly errors, onboarding, next-cron summary."""
from __future__ import annotations

import sys
import types


def _stub_streamlit() -> None:
    if "streamlit" in sys.modules:
        return

    class _Result:
        def __init__(self, *a, **kw): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self): return iter([self, self, self, self])
        def __getattr__(self, _k): return _Result()
        def __call__(self, *a, **kw): return _Result()
        def __getitem__(self, _k): return _Result()

    fake = types.SimpleNamespace()
    for name in (
        "set_page_config", "title", "subheader", "caption", "info", "warning",
        "error", "success", "metric", "divider", "markdown", "dataframe",
        "line_chart", "bar_chart", "text_area", "text_input", "selectbox",
        "slider", "toggle", "checkbox", "button", "spinner", "rerun",
        "expander", "code", "download_button",
    ):
        setattr(fake, name, _Result())
    fake.sidebar = _Result()
    fake.columns = lambda *a, **kw: [_Result() for _ in range(a[0] if a else 4)]
    fake.cache_data = lambda **kw: (lambda f: f)
    sys.modules["streamlit"] = fake


def test_friendly_error_recognises_kis_keys() -> None:
    _stub_streamlit()
    import importlib

    import dashboard.app as da

    importlib.reload(da)
    err = RuntimeError("KIS_APP_KEY / KIS_APP_SECRET not set")
    msg = da._friendly_error(err)
    assert "mais init" in msg or ".env" in msg


def test_friendly_error_recognises_retry_error() -> None:
    _stub_streamlit()
    import importlib

    import dashboard.app as da

    importlib.reload(da)
    err = RuntimeError("RetryError[<Future at 0x...>]")
    msg = da._friendly_error(err)
    assert "mais doctor" in msg or "네트워크" in msg


def test_friendly_error_recognises_pykrx_missing() -> None:
    _stub_streamlit()
    import importlib

    import dashboard.app as da

    importlib.reload(da)
    err = ImportError("No module named 'pykrx'")
    msg = da._friendly_error(err)
    assert "pip install" in msg


def test_friendly_error_falls_back_for_unknown() -> None:
    _stub_streamlit()
    import importlib

    import dashboard.app as da

    importlib.reload(da)
    err = ValueError("unexpected condition")
    msg = da._friendly_error(err)
    assert "ValueError" in msg
    assert "unexpected condition" in msg


def test_next_cron_summary_returns_friendly_string(monkeypatch) -> None:
    _stub_streamlit()
    import importlib

    from common import config as c

    c.load_yaml_settings.cache_clear()
    monkeypatch.setattr(
        "common.config.load_yaml_settings",
        lambda: {
            "scheduler": {
                "research_time": "08:00",
                "order_time": "09:05",
                "eod_review_time": "16:00",
            }
        },
    )

    import dashboard.app as da

    importlib.reload(da)
    out = da._next_cron_summary()
    assert "다음" in out
    assert ("리서치" in out or "주문" in out or "EOD" in out)
    assert "시간" in out and "분" in out
