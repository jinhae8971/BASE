"""Shared test fixtures. Force dry trading mode for all tests."""

from __future__ import annotations

import os

os.environ.setdefault("TRADING_MODE", "dry")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
