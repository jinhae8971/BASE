"""Tests for JSON extraction from Claude responses."""
from __future__ import annotations

import pytest

from src.analyzer import _extract_json


def test_extract_json_clean():
    raw = '{"analyses": [{"coin_id": "x", "pump_thesis": "test"}]}'
    result = _extract_json(raw)
    assert result["analyses"][0]["coin_id"] == "x"


def test_extract_json_with_markdown_fences():
    raw = '```json\n{"analyses": [{"coin_id": "y"}]}\n```'
    result = _extract_json(raw)
    assert result["analyses"][0]["coin_id"] == "y"


def test_extract_json_with_surrounding_prose():
    raw = 'Here is the analysis:\n{"analyses": [{"coin_id": "z"}]}\nEnd.'
    result = _extract_json(raw)
    assert result["analyses"][0]["coin_id"] == "z"


def test_extract_json_with_braces_in_strings():
    raw = '{"analyses": [{"coin_id": "a", "pump_thesis": "price moved {significantly}"}]}'
    result = _extract_json(raw)
    assert result["analyses"][0]["pump_thesis"] == "price moved {significantly}"


def test_extract_json_no_json_raises():
    with pytest.raises(ValueError, match="no JSON"):
        _extract_json("no json here at all")


def test_extract_json_invalid_json_raises():
    with pytest.raises(ValueError):
        _extract_json("{invalid json content")
