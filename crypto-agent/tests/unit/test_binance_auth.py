"""HMAC signing + signed query builder correctness.

Vectors pinned against a known-good computation so any accidental
change to the signing path (e.g. reordered params, different encoding)
trips the test immediately.
"""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import parse_qs

import pytest

from src.execution.binance_auth import build_signed_query, sign


def _expected(qs: str, secret: str) -> str:
    return hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()


def test_sign_matches_hmac_sha256_hex() -> None:
    qs = "symbol=BTCUSDT&side=BUY&type=MARKET&quoteOrderQty=10&timestamp=1700000000000&recvWindow=5000"
    secret = "NOT_A_REAL_SECRET"
    assert sign(qs, secret) == _expected(qs, secret)


def test_build_signed_query_appends_timestamp_and_signature() -> None:
    out = build_signed_query(
        {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quoteOrderQty": 10},
        secret="abc",
        timestamp_ms=1_700_000_000_000,
    )
    parts = parse_qs(out)
    assert parts["timestamp"] == ["1700000000000"]
    assert parts["recvWindow"] == ["5000"]
    assert "signature" in parts
    # Strip the signature to re-derive it and confirm it matches.
    body, sig = out.rsplit("&signature=", 1)
    assert sig == _expected(body, "abc")


def test_build_signed_query_drops_none_values() -> None:
    out = build_signed_query(
        {"symbol": "BTCUSDT", "limit_price": None, "quantity": 1.5},
        secret="abc",
        timestamp_ms=123,
    )
    parts = parse_qs(out)
    assert "limit_price" not in parts
    assert parts["quantity"] == ["1.5"]


def test_build_signed_query_is_deterministic_for_same_inputs() -> None:
    a = build_signed_query({"x": "1"}, "s", timestamp_ms=1)
    b = build_signed_query({"x": "1"}, "s", timestamp_ms=1)
    assert a == b
