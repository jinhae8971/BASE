"""Rank top-K 48h gainers from market data + 2-day-old snapshot."""
from __future__ import annotations

from .config import get_settings
from .logging_setup import get_logger
from .models import CoinMarket, DailySnapshot, GainerCoin

log = get_logger(__name__)

# Stablecoins and wrapped/derivative assets to exclude from gainer rankings.
EXCLUDED_SYMBOLS: set[str] = {
    # Stablecoins
    "usdt", "usdc", "dai", "tusd", "usdp", "usdd", "fdusd", "pyusd", "usde",
    "frax", "lusd", "gusd", "busd", "usdb", "crvusd", "susds",
    # Wrapped / staking derivatives
    "wbtc", "weth", "steth", "wsteth", "reth", "cbeth", "wbeth", "ezeth",
    "weeth", "sfrxeth", "sweth", "meth", "rseth", "lseth",
    # Wrapped natives / bridged
    "wbnb", "wmatic", "wavax", "wftm", "wsol", "wtrx", "wxrp",
}


def _is_excluded(symbol: str) -> bool:
    return symbol.lower() in EXCLUDED_SYMBOLS


def rank_top_gainers(
    markets: list[CoinMarket],
    prior_snapshot: DailySnapshot | None,
) -> list[GainerCoin]:
    """Compute 48h change vs. prior_snapshot and return top-K gainers.

    If no prior snapshot exists (cold start), falls back to 24h change from
    CoinGecko so the pipeline still produces output.
    """
    settings = get_settings()
    prior_by_id: dict[str, float] = {}
    if prior_snapshot is not None:
        prior_by_id = {c.id: c.price for c in prior_snapshot.coins if c.price > 0}

    candidates: list[GainerCoin] = []
    fallback_mode = not prior_by_id
    if fallback_mode:
        log.warning("no prior snapshot found; falling back to 24h change")

    for m in markets:
        if _is_excluded(m.symbol):
            continue
        if m.total_volume < settings.min_volume_usd:
            continue

        if fallback_mode:
            change = m.price_change_percentage_24h
            prior_price = None
        else:
            prior_price = prior_by_id.get(m.id)
            if prior_price is None or prior_price <= 0:
                continue
            change = (m.current_price - prior_price) / prior_price * 100.0

        if change is None or change <= 0:
            continue

        candidates.append(
            GainerCoin(
                id=m.id,
                symbol=m.symbol,
                name=m.name,
                image=m.image,
                current_price=m.current_price,
                market_cap=m.market_cap,
                market_cap_rank=m.market_cap_rank,
                total_volume=m.total_volume,
                change_24h_pct=m.price_change_percentage_24h,
                change_48h_pct=float(change),
                price_48h_ago=prior_price,
            )
        )

    candidates.sort(key=lambda c: c.change_48h_pct, reverse=True)
    top = candidates[: settings.top_k_gainers]
    log.info(
        "ranked %d candidates, selected top %d (fallback=%s)",
        len(candidates),
        len(top),
        fallback_mode,
    )
    return top
