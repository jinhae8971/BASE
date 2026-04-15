"""Historical snapshot provider.

Given a universe of symbols and a multi-day candle history, produce an
as-of `MarketSnapshot` for any date in the range. The provider is
intentionally schema-compatible with `data.snapshot.gather()` so
`DailyWorkflow` doesn't know it's running inside a backtest.

Non-candle snapshot fields (markets, categories, chain_tvl, news, macro)
are derived where possible from candles or left empty -- Phase 4 focuses on
the price/quant loop; Phase 5+ can layer in historical CoinGecko/FRED/etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.data.binance_md import Candle
from src.data.coingecko import MarketRow
from src.data.fred import MacroSnapshot
from src.data.snapshot import MarketSnapshot


@dataclass
class HistoricalSnapshotProvider:
    """Owns the full historical candle archive and serves as-of slices."""

    universe: list[str]
    candles_by_symbol: dict[str, list[Candle]]
    #: Optional per-day macro injection. Maps ISO date -> MacroSnapshot.values
    macro_by_day: dict[str, dict[str, float]] = field(default_factory=dict)
    warmup_candles: int = 30

    def __post_init__(self) -> None:
        # Cache sorted dates for binary-search slicing.
        self._dates_by_symbol: dict[str, list[datetime]] = {
            s: [c.open_time for c in cs] for s, cs in self.candles_by_symbol.items()
        }

    # --- engine-facing API -------------------------------------------

    def close_prices(self, day: datetime) -> dict[str, float]:
        out: dict[str, float] = {}
        for sym, cs in self.candles_by_symbol.items():
            c = _last_at_or_before(cs, day)
            if c is not None:
                out[sym] = c.close
        return out

    def btc_close(self, day: datetime) -> float:
        cs = self.candles_by_symbol.get("BTCUSDT") or []
        c = _last_at_or_before(cs, day)
        return c.close if c else 0.0

    def trading_days(self) -> list[datetime]:
        """Union of candle dates across symbols, sorted ascending, warmup-aware."""
        all_dates: set[datetime] = set()
        for cs in self.candles_by_symbol.values():
            for c in cs:
                all_dates.add(c.open_time)
        ordered = sorted(all_dates)
        return ordered[self.warmup_candles :]

    def snapshot_as_of(self, day: datetime) -> MarketSnapshot:
        snap = MarketSnapshot(as_of=day)
        snap.universe = list(self.universe)
        snap.candles = {
            sym: _slice_up_to(cs, day)
            for sym, cs in self.candles_by_symbol.items()
            if sym in self.universe
        }
        # Derive minimal CoinGecko-shaped markets list from the last-candle
        # close so the Research/Value/Executor agents have *something*.
        snap.markets = _derive_markets(snap.candles)
        snap.categories = []
        snap.chain_tvl = []
        snap.news = []

        macro_values = self.macro_by_day.get(day.date().isoformat(), {})
        snap.macro = MacroSnapshot(values=dict(macro_values), as_of={})
        return snap

    def snapshot_fn(self, day: datetime):
        """Return a closure matching `data.snapshot.gather`'s signature.

        `DailyWorkflow` calls `await self._snapshot_fn(universe_size=...)` --
        accept and ignore the kwargs so the signature matches exactly.
        """

        async def _snap(**kwargs: Any) -> MarketSnapshot:
            return self.snapshot_as_of(day)

        return _snap


def _last_at_or_before(candles: list[Candle], day: datetime) -> Candle | None:
    # Linear scan is fine for daily data over a few years. Keep it simple.
    found: Candle | None = None
    for c in candles:
        if c.open_time <= day:
            found = c
        else:
            break
    return found


def _slice_up_to(candles: list[Candle], day: datetime) -> list[Candle]:
    return [c for c in candles if c.open_time <= day]


def _derive_markets(candles: dict[str, list[Candle]]) -> list[MarketRow]:
    rows: list[MarketRow] = []
    for sym, cs in candles.items():
        if not cs:
            continue
        base = sym[:-4] if sym.endswith("USDT") else sym
        last = cs[-1]
        prev_24 = cs[-2] if len(cs) >= 2 else last
        prev_7d = cs[-8] if len(cs) >= 8 else last
        chg_24 = 100.0 * (last.close / prev_24.close - 1) if prev_24.close else 0.0
        chg_7d = 100.0 * (last.close / prev_7d.close - 1) if prev_7d.close else 0.0
        rows.append(
            MarketRow(
                id=base.lower(),
                symbol=base,
                name=base,
                market_cap=last.close * 1e9,   # nominal -- we lack real MC historically
                fdv=last.close * 1.1e9,
                volume_24h=last.quote_volume,
                price_change_24h_pct=chg_24,
                price_change_7d_pct=chg_7d,
            )
        )
    return rows
