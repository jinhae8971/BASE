"""Rank top-K N-trading-day gainers from market data + prior snapshot."""
from __future__ import annotations

from .config import get_settings
from .logging_setup import get_logger
from .models import DailySnapshot, GainerStock, StockMarket

log = get_logger(__name__)


def rank_top_gainers(
    stocks: list[StockMarket],
    prior_snapshot: DailySnapshot | None,
) -> list[GainerStock]:
    """Compute N-trading-day change vs. prior_snapshot and return top-K gainers.

    If no prior snapshot exists (cold start), falls back to the single-day
    change_pct from KRX data so the pipeline still produces output.
    """
    settings = get_settings()
    prior_by_ticker: dict[str, float] = {}
    if prior_snapshot is not None:
        prior_by_ticker = {s.ticker: s.close for s in prior_snapshot.stocks if s.close > 0}

    fallback_mode = not prior_by_ticker
    if fallback_mode:
        log.warning("no prior snapshot; falling back to single-day change_pct")

    # Sort by market cap descending to assign rank
    stocks_sorted = sorted(stocks, key=lambda s: s.market_cap, reverse=True)
    rank_map = {s.ticker: i + 1 for i, s in enumerate(stocks_sorted)}

    candidates: list[GainerStock] = []
    for s in stocks:
        if s.trading_value < settings.min_volume_krw:
            continue

        if fallback_mode:
            change = s.change_pct
            prior_price = None
        else:
            prior_price = prior_by_ticker.get(s.ticker)
            if prior_price is None or prior_price <= 0:
                continue
            change = (s.close - prior_price) / prior_price * 100.0

        if change <= 0:
            continue

        candidates.append(
            GainerStock(
                ticker=s.ticker,
                name=s.name,
                market=s.market,
                close=s.close,
                market_cap=s.market_cap,
                trading_value=s.trading_value,
                volume=s.volume,
                change_pct_1d=s.change_pct,
                change_pct_nd=float(change),
                price_n_days_ago=prior_price,
                market_cap_rank=rank_map.get(s.ticker),
            )
        )

    candidates.sort(key=lambda c: c.change_pct_nd, reverse=True)
    top = candidates[: settings.top_k_gainers]
    log.info(
        "ranked %d candidates, selected top %d (fallback=%s)",
        len(candidates),
        len(top),
        fallback_mode,
    )
    return top
