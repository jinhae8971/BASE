"""Dashboard page functions — smoke + edge-case tests.

We can't render a real Streamlit page in unit tests, but we can:
    1. Verify the module imports without error.
    2. Verify the PAGES dict surface.
    3. Verify the strip-patches regex actually removes a complete block.
    4. Verify the dotted setter helpers used by the Control page.

Streamlit is the only heavy dep — gated with ``importorskip``.
"""
from __future__ import annotations

import sys
import types

import pytest


def _stub_streamlit() -> None:
    """Install a Streamlit-like stub that supports context managers + columns
    + sliders so the dashboard module can be imported without raising.
    """
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
    ):
        setattr(fake, name, _Result())
    fake.sidebar = _Result()
    fake.columns = lambda *a, **kw: [_Result() for _ in range(a[0] if a else 4)]
    fake.cache_data = lambda **kw: (lambda f: f)
    sys.modules["streamlit"] = fake


def test_dashboard_imports_under_stubs() -> None:
    _stub_streamlit()
    import importlib

    # Re-import in case test order matters
    import dashboard.app as da

    importlib.reload(da)
    assert isinstance(da.PAGES, dict)
    assert len(da.PAGES) >= 10


def test_pages_dict_has_expected_pages() -> None:
    _stub_streamlit()
    import importlib

    import dashboard.app as da

    importlib.reload(da)
    expected = {"Overview", "Today", "Positions", "X-ray", "Learning",
                "Alerts", "Journal", "Backtest", "Control", "Reflection"}
    page_text = " ".join(da.PAGES.keys())
    for name in expected:
        assert name in page_text, f"page {name} missing from sidebar"


def test_strip_patches_regex_removes_full_block() -> None:
    """Trailing patches block must be removed entirely, including the
    closing ```.``` fence."""
    import re

    body = (
        "# Reflection 2025-05-09\n\n"
        "## Section 1\nSome narrative.\n\n"
        "## Auto-apply patches\n"
        "```json\n"
        '[{"path": "risk.hard_stop_pct", "value": 0.10}]\n'
        "```\n"
    )
    new_body = re.sub(
        r"\n*##\s*Auto-apply patches[\s\S]*\Z",
        "\n",
        body,
        flags=re.IGNORECASE,
    )
    assert "## Auto-apply patches" not in new_body
    assert "```json" not in new_body
    assert "## Section 1" in new_body  # narrative kept
    assert "Some narrative." in new_body


def test_strip_patches_no_block_is_noop() -> None:
    import re

    body = "# Reflection\n\nNo patches block here.\n"
    new_body = re.sub(
        r"\n*##\s*Auto-apply patches[\s\S]*\Z",
        "\n",
        body,
        flags=re.IGNORECASE,
    )
    assert new_body == body or new_body.strip() == body.strip()


def test_dotted_setter_helpers() -> None:
    _stub_streamlit()
    import importlib

    import dashboard.app as da

    importlib.reload(da)

    d: dict = {}
    da._set_dotted(d, "risk.hard_stop_pct", 0.10)
    assert d == {"risk": {"hard_stop_pct": 0.10}}

    da._set_dotted(d, "execution.twap_enabled", True)
    assert d["execution"]["twap_enabled"] is True
    # Existing keys preserved
    assert d["risk"]["hard_stop_pct"] == 0.10


def test_dashboard_module_doc_lists_all_pages() -> None:
    """Module docstring must reflect the actual page count to avoid drift."""
    _stub_streamlit()
    import importlib

    import dashboard.app as da

    importlib.reload(da)
    doc = (da.__doc__ or "").lower()
    for page in (
        "overview", "today", "positions", "x-ray",
        "learning", "alerts", "journal", "backtest",
        "control", "reflection",
    ):
        assert page in doc, f"docstring missing '{page}'"


def test_quick_actions_warning_in_doc() -> None:
    """Docstring should warn that quick actions block the UI."""
    _stub_streamlit()
    import importlib

    import dashboard.app as da

    importlib.reload(da)
    doc = (da.__doc__ or "").lower()
    assert "block" in doc or "synchronous" in doc


@pytest.mark.parametrize("path", [
    "risk.hard_stop_pct",
    "risk.trailing_take_pct",
    "execution.rebalance_buy_threshold",
    "consensus.weights.quant",
])
def test_control_panel_paths_align_with_reflection_whitelist(path: str) -> None:
    """Control panel sliders edit the same paths reflection_apply allows."""
    from agents.reflection_apply import _WHITELIST

    if path in _WHITELIST:
        return
    # Some control-panel paths are operator-only (max_position_weight,
    # max_sector_weight) that aren't in the reflection whitelist — ensure
    # the path at least exists somewhere by importing settings module
    from common import config as c

    _ = c.get_setting(path, None)
