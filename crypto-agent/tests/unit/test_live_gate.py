"""Live mode safety gate tests -- the single most security-critical module.

Every test resets TRADING_MODE to `live`, asserts the precise behavior
of each lock, then leaves state clean for the next test via monkeypatch
so we never depend on fixture ordering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import get_settings
from src.execution.live_gate import (
    ACK_FILE,
    ACK_PHRASE,
    LiveNotPromoted,
    WithdrawEnabled,
    assert_live_allowed,
    assert_withdraw_disabled,
    cap_equity_usd,
    cap_order_usd,
)


@pytest.fixture
def _live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "live")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    monkeypatch.setenv("TRADING_MODE", "dry")
    get_settings.cache_clear()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Promotion gate
# ---------------------------------------------------------------------------


def test_assert_live_allowed_is_noop_in_dry_mode() -> None:
    # Dry mode by default in conftest -- this should just return without raising.
    assert_live_allowed()


def test_live_mode_requires_promote_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _live_mode: None
) -> None:
    monkeypatch.delenv("PROMOTE_LIVE", raising=False)
    with pytest.raises(LiveNotPromoted, match="PROMOTE_LIVE"):
        assert_live_allowed(repo_root=tmp_path)


def test_live_mode_requires_ack_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _live_mode: None
) -> None:
    monkeypatch.setenv("PROMOTE_LIVE", "1")
    with pytest.raises(LiveNotPromoted, match="ack file missing"):
        assert_live_allowed(repo_root=tmp_path)


def test_live_mode_requires_exact_ack_phrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _live_mode: None
) -> None:
    monkeypatch.setenv("PROMOTE_LIVE", "1")
    (tmp_path / ACK_FILE).write_text("whatever\n", encoding="utf-8")
    with pytest.raises(LiveNotPromoted, match="exactly"):
        assert_live_allowed(repo_root=tmp_path)


def test_live_mode_passes_with_all_three_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _live_mode: None
) -> None:
    monkeypatch.setenv("PROMOTE_LIVE", "1")
    (tmp_path / ACK_FILE).write_text(ACK_PHRASE + "\n", encoding="utf-8")
    assert_live_allowed(repo_root=tmp_path)  # no raise


# ---------------------------------------------------------------------------
# Withdraw refusal
# ---------------------------------------------------------------------------


def test_assert_withdraw_disabled_passes_when_disabled() -> None:
    assert_withdraw_disabled({"canWithdraw": False, "balances": []})


def test_assert_withdraw_disabled_raises_when_enabled() -> None:
    with pytest.raises(WithdrawEnabled, match="Withdraw permission ENABLED"):
        assert_withdraw_disabled({"canWithdraw": True, "balances": []})


def test_assert_withdraw_disabled_raises_when_field_missing() -> None:
    # Missing field is treated as enabled -- fail-closed.
    with pytest.raises(WithdrawEnabled):
        assert_withdraw_disabled({"balances": []})


# ---------------------------------------------------------------------------
# Hard caps
# ---------------------------------------------------------------------------


def test_cap_order_usd_passthrough_in_dry_mode() -> None:
    # 10_000 is well above the default live cap, but we're in dry mode.
    assert cap_order_usd(10_000.0) == 10_000.0


def test_cap_order_usd_clamps_in_live_mode(
    monkeypatch: pytest.MonkeyPatch, _live_mode: None
) -> None:
    monkeypatch.setenv("LIVE_MAX_ORDER_USDT", "100")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    assert cap_order_usd(500.0) == 100.0
    assert cap_order_usd(50.0) == 50.0  # below cap, passthrough


def test_cap_equity_usd_clamps_account_equity_in_live_mode(
    monkeypatch: pytest.MonkeyPatch, _live_mode: None
) -> None:
    monkeypatch.setenv("LIVE_MAX_CAPITAL_USDT", "1000")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    # Account has $5k worth of assets but we only authorized $1k.
    assert cap_equity_usd(5_000.0) == 1_000.0
    # Under the cap, passthrough.
    assert cap_equity_usd(800.0) == 800.0
