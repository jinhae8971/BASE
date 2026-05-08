"""Contract tests — verify each agent parses a well-formed LLM response.

We can't unit-test the *quality* of the LLM output here, but we can verify
that the parsing layer (``parse_response``) accepts the JSON shape the
prompt asks for. If the prompt format ever drifts, this test catches it.
"""
from __future__ import annotations

import json
from datetime import date

from agents import MacroAgent, QuantAgent, SectorAgent, ValueAgent
from common.types import MarketRegime, Side


def _wrap(payload: dict) -> str:
    return f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"


def test_macro_agent_parses_well_formed_response() -> None:
    raw = _wrap(
        {
            "regime": "risk_on",
            "equity_weight": 0.85,
            "conviction": 8,
            "rationale": "Loose financial conditions, KOSPI 3m mom +6%",
        }
    )
    p = MacroAgent().parse_response(raw, date(2025, 5, 6))
    assert p.regime is MarketRegime.RISK_ON
    assert p.equity_weight == 0.85
    assert p.conviction == 8


def test_macro_agent_falls_back_on_unknown_regime() -> None:
    raw = _wrap(
        {
            "regime": "unknown_label",
            "equity_weight": 0.5,
            "conviction": 5,
            "rationale": "x",
        }
    )
    p = MacroAgent().parse_response(raw, date(2025, 5, 6))
    assert p.regime is MarketRegime.NEUTRAL


def test_sector_agent_parses_tilts() -> None:
    raw = _wrap(
        {
            "conviction": 7,
            "rationale": "tech leading",
            "sector_tilts": [
                {"sector": "반도체", "tilt": 0.05},
                {"sector": "금융", "tilt": -0.03},
            ],
        }
    )
    p = SectorAgent().parse_response(raw, date(2025, 5, 6))
    assert p.sector_tilts["반도체"] == 0.05
    assert p.sector_tilts["금융"] == -0.03


def test_value_agent_parses_picks() -> None:
    raw = _wrap(
        {
            "conviction": 8,
            "rationale": "deep value cycle",
            "picks": [
                {
                    "ticker": "005930",
                    "name": "삼성전자",
                    "side": "BUY",
                    "target_weight": 0.08,
                    "score": 8.5,
                    "rationale": "PER 9, MoS 40%",
                }
            ],
        }
    )
    p = ValueAgent().parse_response(raw, date(2025, 5, 6))
    assert len(p.picks) == 1
    assert p.picks[0].ticker == "005930"
    assert p.picks[0].side is Side.BUY
    assert p.picks[0].target_weight == 0.08


def test_quant_agent_parses_picks() -> None:
    raw = _wrap(
        {
            "conviction": 7,
            "rationale": "M+B factor tailwind",
            "picks": [
                {
                    "ticker": "000660",
                    "name": "SK하이닉스",
                    "side": "BUY",
                    "target_weight": 0.06,
                    "score": 1.7,
                    "rationale": "M=+1.8 V=+0.4",
                },
                {
                    "ticker": "035420",
                    "name": "NAVER",
                    "side": "BUY",
                    "target_weight": 0.05,
                    "score": 1.2,
                    "rationale": "M=+1.0 Q=+0.5",
                },
            ],
        }
    )
    p = QuantAgent().parse_response(raw, date(2025, 5, 6))
    assert len(p.picks) == 2
    assert {pick.ticker for pick in p.picks} == {"000660", "035420"}


def test_extract_json_strips_code_fences() -> None:
    """Prompts ask for JSON-only, but Claude sometimes wraps in fences."""
    raw = "Sure, here is the JSON:\n```json\n{\"a\": 1}\n```"
    out = MacroAgent._extract_json(raw)
    assert out == {"a": 1}


def test_extract_json_raises_on_missing_braces() -> None:
    """Malformed responses must raise so the pipeline notifies and skips."""
    import pytest

    with pytest.raises(ValueError):
        MacroAgent._extract_json("no json here")
