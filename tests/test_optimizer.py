from __future__ import annotations

from datetime import date

from common.types import MarketRegime
from orchestrator.optimizer import PortfolioOptimizer


def _consensus() -> dict:
    return {
        "as_of": date(2025, 1, 15),
        "equity_weight": 0.8,
        "regime": MarketRegime.RISK_ON,
        "sector_tilts": {},
        "ticker_scores": {
            "005930": {"score": 2.0, "mentions": 3, "rationale": ""},
            "000660": {"score": 1.5, "mentions": 2, "rationale": ""},
            "035420": {"score": 1.0, "mentions": 2, "rationale": ""},
        },
    }


def test_position_cap_enforced() -> None:
    opt = PortfolioOptimizer()
    target = opt.optimize(_consensus())
    for w in target.positions.values():
        assert w <= opt.max_pos + 1e-9
    assert target.cash_weight >= 0
    # Total weights should not exceed 1
    assert sum(target.positions.values()) + target.cash_weight <= 1.0 + 1e-6


def test_sector_cap_enforced() -> None:
    opt = PortfolioOptimizer()
    sector_map = {"005930": "반도체", "000660": "반도체", "035420": "인터넷"}
    # Force heavy tilt to 반도체
    cons = _consensus()
    cons["ticker_scores"]["005930"]["score"] = 10
    cons["ticker_scores"]["000660"]["score"] = 10
    target = opt.optimize(cons, sector_map=sector_map)
    semis = target.positions.get("005930", 0) + target.positions.get("000660", 0)
    assert semis <= opt.max_sector + 1e-6


def test_empty_scores_returns_all_cash() -> None:
    opt = PortfolioOptimizer()
    cons = _consensus()
    cons["ticker_scores"] = {}
    target = opt.optimize(cons)
    assert target.cash_weight == 1.0
    assert target.positions == {}
