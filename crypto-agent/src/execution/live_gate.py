"""Multi-layer safety gate for live trading.

Turning on `TRADING_MODE=live` is **not enough by itself** to place real
orders. The gate requires THREE independent affirmative conditions
before a live order can leave the process:

  1. `TRADING_MODE=live` in the environment (explicit intent)
  2. `PROMOTE_LIVE=1` in the environment (explicit promotion signal)
  3. A `.live-promotion-ack` file at the repo root containing the exact
     string
         "I UNDERSTAND THE RISK OF LIVE TRADING"
     The file is persistent and grep-able in shell history so there is no
     way to "accidentally" flip into live mode via `--live` somewhere.

Additionally, the gate enforces two hard caps that are independent of
the executor agent's decisions:

  - `LIVE_MAX_ORDER_USDT`  : per-order notional ceiling (clamps qty_usd)
  - `LIVE_MAX_CAPITAL_USDT`: equity ceiling (clamps account_equity_usd
                             so the sizing math can never size against
                             funds the operator didn't opt in to put at
                             risk)

And one hard refusal:

  - On the first authenticated account call the gate verifies that the
    API key's `canWithdraw` flag is **False**. If withdraw is enabled we
    raise `WithdrawEnabled` and REFUSE to place any order. The operator
    must disable Withdraw in the Binance API dashboard and regenerate
    keys before retry.

This module is pure (no network I/O) -- it consumes an account snapshot
from the caller (the authenticated Binance client) so tests stay
hermetic.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.config import TradingMode, get_settings
from src.logging import get_logger

log = get_logger("live_gate")

PROMOTE_ENV = "PROMOTE_LIVE"
ACK_FILE = ".live-promotion-ack"
ACK_PHRASE = "I UNDERSTAND THE RISK OF LIVE TRADING"


class LiveNotPromoted(RuntimeError):
    """Live mode requested without fulfilling all three promotion conditions."""


class WithdrawEnabled(RuntimeError):
    """API key has Withdraw permission ON -- refuse to trade."""


def assert_live_allowed(repo_root: Path | None = None) -> None:
    """Raise if live mode is not fully promoted. No-op outside live mode."""
    s = get_settings()
    if s.trading_mode != TradingMode.LIVE:
        return

    if os.environ.get(PROMOTE_ENV, "") != "1":
        raise LiveNotPromoted(f"{PROMOTE_ENV}=1 required for live mode")

    ack_path = (repo_root or Path.cwd()) / ACK_FILE
    if not ack_path.exists():
        raise LiveNotPromoted(f"live promotion ack file missing: {ack_path}")

    content = ack_path.read_text(encoding="utf-8").strip()
    if content != ACK_PHRASE:
        raise LiveNotPromoted(
            f"ack file must contain exactly: '{ACK_PHRASE}'"
        )


def assert_withdraw_disabled(account_info: dict) -> None:
    """Enforce that the API key used for live trading cannot withdraw funds.

    The caller passes the response of `GET /api/v3/account`. Binance
    returns `canWithdraw: bool`. If True, we raise -- no orders allowed.
    """
    can_withdraw = bool(account_info.get("canWithdraw", True))
    if can_withdraw:
        raise WithdrawEnabled(
            "API key has Withdraw permission ENABLED. Disable Withdraw in "
            "the Binance API dashboard and regenerate the key before live trading."
        )


def cap_order_usd(qty_usd: float) -> float:
    """Clamp a live order's notional to `LIVE_MAX_ORDER_USDT`.

    Pass-through in dry and paper modes -- those paths already have their
    own sanity checks and the cap is a live-mode-specific backstop.
    """
    s = get_settings()
    if s.trading_mode != TradingMode.LIVE:
        return qty_usd
    cap = float(s.live_max_order_usdt)
    if qty_usd > cap:
        log.warning("live_gate.order_capped", requested=qty_usd, cap=cap)
        return cap
    return qty_usd


def cap_equity_usd(equity_usd: float) -> float:
    """Clamp reported account equity to `LIVE_MAX_CAPITAL_USDT`.

    This is the most important clamp in the module: the system always
    sizes positions as a percentage of reported equity, so if we never
    tell downstream code that the account has more than $1,000, it
    cannot construct an order that exceeds $1,000 worth of exposure
    (subject also to the per-order cap).
    """
    s = get_settings()
    if s.trading_mode != TradingMode.LIVE:
        return equity_usd
    cap = float(s.live_max_capital_usdt)
    if equity_usd > cap:
        log.info("live_gate.equity_capped", actual=equity_usd, cap=cap)
        return cap
    return equity_usd
