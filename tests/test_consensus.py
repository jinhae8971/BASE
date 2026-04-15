from __future__ import annotations

from datetime import date

from common.types import AgentProposal, MarketRegime, Side, TickerView
from orchestrator.consensus import Consensus


def _macro(conv: int = 8, weight: float = 0.8) -> AgentProposal:
    return AgentProposal(
        agent_name="macro",
        as_of=date(2025, 1, 15),
        conviction=conv,
        rationale="loose financial conditions",
        equity_weight=weight,
        regime=MarketRegime.RISK_ON,
    )


def _value(conv: int = 7) -> AgentProposal:
    return AgentProposal(
        agent_name="value",
        as_of=date(2025, 1, 15),
        conviction=conv,
        rationale="deep value in semis",
        picks=[
            TickerView(
                ticker="005930",
                name="삼성전자",
                side=Side.BUY,
                target_weight=0.08,
                score=9.0,
                rationale="PER 9x, MoS 40%",
            ),
            TickerView(
                ticker="000660",
                name="SK하이닉스",
                side=Side.BUY,
                target_weight=0.05,
                score=7.5,
                rationale="FCF recovery",
            ),
        ],
    )


def _quant(conv: int = 7) -> AgentProposal:
    return AgentProposal(
        agent_name="quant",
        as_of=date(2025, 1, 15),
        conviction=conv,
        rationale="factor tailwind",
        picks=[
            TickerView(ticker="005930", score=1.5, side=Side.BUY),
            TickerView(ticker="035420", score=1.1, side=Side.BUY),
        ],
    )


def test_consensus_produces_expected_shape() -> None:
    c = Consensus()
    out = c.aggregate([_macro(), _value(), _quant()], date(2025, 1, 15))
    assert 0 <= out["equity_weight"] <= 1
    assert out["regime"] == MarketRegime.RISK_ON
    assert "005930" in out["ticker_scores"]


def test_consensus_drops_low_conviction() -> None:
    c = Consensus()
    out = c.aggregate(
        [_macro(conv=2), _value(conv=2), _quant(conv=2)],
        date(2025, 1, 15),
    )
    # Falls back to default neutral view
    assert out["equity_weight"] == 0.5
    assert out["regime"] == MarketRegime.NEUTRAL


def test_consensus_ticker_scoring_weighted() -> None:
    c = Consensus()
    out = c.aggregate([_value(conv=10), _quant(conv=10)], date(2025, 1, 15))
    scores = out["ticker_scores"]
    assert scores["005930"]["mentions"] == 2
    assert scores["005930"]["score"] > scores.get("035420", {"score": 0})["score"]
