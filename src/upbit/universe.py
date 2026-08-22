"""Tradable universe construction for the KRW market.

Pipeline: all markets → KRW only → drop 유의/주의 → drop stablecoins and the
manual blacklist → drop 장기보유 → liquidity floor → keep the top-N by 24h
거래대금 for the deep scan.

Upbit has shipped two shapes of the warning flag over time (``market_warning``
and the newer nested ``market_event``); :func:`market_flags` normalises both.
"""
from __future__ import annotations

from typing import Any

from common.logging import get_logger

from .client import UpbitClient
from .holdings import HoldingsGuard
from .strategy import UniverseConfig
from .types import Candidate  # noqa: F401 - re-exported for convenience

log = get_logger(__name__)


def market_flags(market: dict[str, Any]) -> tuple[bool, set[str]]:
    """Return ``(is_warning, active_caution_types)`` for a `/v1/market/all` entry.

    Upbit reports 주의 as a map of named conditions, e.g.
    ``{"PRICE_FLUCTUATIONS": false, "TRADING_VOLUME_SOARING": true, ...}``.
    Callers decide which of those actually disqualify a coin — see
    :attr:`~upbit.strategy.UniverseConfig.caution_types`.
    """
    event = market.get("market_event") or {}
    warning = bool(event.get("warning", False))

    cautions: set[str] = set()
    caution_map = event.get("caution")
    if isinstance(caution_map, dict):
        cautions = {str(k).upper() for k, v in caution_map.items() if v}

    legacy = str(market.get("market_warning") or "").upper()
    if legacy == "CAUTION":
        # The older endpoint shape does not say *which* condition tripped.
        cautions.add("CAUTION")
    elif legacy not in ("", "NONE"):
        warning = True
    return warning, cautions


class UniverseBuilder:
    def __init__(self, client: UpbitClient, config: UniverseConfig, guard: HoldingsGuard) -> None:
        self.client = client
        self.config = config
        self.guard = guard

    # ------------------------------------------------------------------
    def eligible_markets(self, quote: str = "KRW") -> tuple[list[str], dict[str, str], list[dict]]:
        """Static screen. Returns ``(markets, korean_names, rejections)``."""
        cfg = self.config
        blacklist = {s.strip().upper() for s in cfg.manual_blacklist if s.strip()}
        stablecoins = {s.strip().upper() for s in cfg.stablecoins if s.strip()}
        disqualifying_cautions = {s.strip().upper() for s in cfg.caution_types if s.strip()}

        markets: list[str] = []
        names: dict[str, str] = {}
        rejected: list[dict[str, Any]] = []

        for entry in self.client.get_markets(is_details=True):
            code = str(entry.get("market", ""))
            if not code.startswith(f"{quote}-"):
                continue
            symbol = code.split("-")[-1].upper()
            names[code] = entry.get("korean_name") or symbol

            warning, cautions = market_flags(entry)
            blocking = cautions & disqualifying_cautions
            reason: str | None = None
            # 장기보유 first: it is the user's own explicit instruction, so it must
            # be the reason reported even when an exchange flag would also catch
            # the coin — otherwise the 분석내역 log hides why it was really skipped.
            if self.guard.is_protected(symbol):
                reason = "장기보유 코인 (거래 제외)"
            elif symbol in blacklist:
                reason = "수동 제외 목록"
            elif cfg.exclude_warning and warning:
                reason = "유의 종목"
            elif cfg.exclude_caution and blocking:
                reason = f"주의 종목 ({', '.join(sorted(blocking))})"
            elif cfg.exclude_stablecoins and symbol in stablecoins:
                reason = "스테이블코인"

            if reason:
                rejected.append({"market": code, "symbol": symbol, "reason": reason})
                continue
            markets.append(code)

        log.info(
            "upbit.universe.static_screen",
            eligible=len(markets),
            rejected=len(rejected),
            long_term=len(self.guard.symbols),
        )
        return markets, names, rejected

    # ------------------------------------------------------------------
    def liquid_candidates(
        self, markets: list[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Liquidity screen on live tickers. Returns ``(kept, rejected)``."""
        if not markets:
            return [], []

        cfg = self.config
        tickers = self.client.get_tickers(markets)
        kept: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for t in tickers:
            code = t.get("market", "")
            turnover = float(t.get("acc_trade_price_24h") or 0)
            price = float(t.get("trade_price") or 0)

            if turnover < cfg.min_trade_price_24h:
                rejected.append(
                    {
                        "market": code,
                        "symbol": code.split("-")[-1],
                        "reason": f"거래대금 미달 ({turnover / 1e8:.1f}억)",
                    }
                )
                continue
            if cfg.max_trade_price_24h and turnover > cfg.max_trade_price_24h:
                rejected.append({"market": code, "symbol": code.split("-")[-1], "reason": "거래대금 상한 초과"})
                continue
            if price < cfg.min_price:
                rejected.append({"market": code, "symbol": code.split("-")[-1], "reason": "최소 가격 미달"})
                continue
            kept.append(t)

        kept.sort(key=lambda t: float(t.get("acc_trade_price_24h") or 0), reverse=True)
        overflow = kept[cfg.max_candidates :]
        rejected.extend(
            {
                "market": t.get("market", ""),
                "symbol": str(t.get("market", "")).split("-")[-1],
                "reason": f"거래대금 순위 {cfg.max_candidates}위 밖",
            }
            for t in overflow
        )
        kept = kept[: cfg.max_candidates]

        log.info("upbit.universe.liquidity_screen", kept=len(kept), rejected=len(rejected))
        return kept, rejected

    # ------------------------------------------------------------------
    def build(self, quote: str = "KRW") -> dict[str, Any]:
        """Run both screens and hand the engine everything it needs."""
        markets, names, static_rejects = self.eligible_markets(quote)
        tickers, liquidity_rejects = self.liquid_candidates(markets)
        return {
            "tickers": tickers,
            "korean_names": names,
            "rejected": static_rejects + liquidity_rejects,
            "screened": len(markets),
            "candidates": len(tickers),
        }
