"""The day-trading engine — scan, select, enter, monitor, exit.

Two cycles drive everything:

``run_selection()``   fires once a day (09:10 KST by default). It reads the BTC
                      regime, screens the KRW universe, scores every survivor,
                      and opens positions in the best names that clear both the
                      score floor and the risk guards.

``monitor_positions()`` fires every few minutes. It re-prices every open
                      position and closes the ones that hit take-profit,
                      stop-loss, the trailing stop, the holding-time limit, or a
                      regime flip.

Scoring is deliberately two-stage so a 60-name universe costs ~80 API calls
instead of ~240: the cheap pillars (거래량 · 차트 · 베타) run off daily candles
for everyone, then only the shortlist pays for orderbook, tick and intraday data
to compute 수급.

장기보유 코인은 두 단계 모두에서 배제된다 — 유니버스에서 걸러지고, 청산 로직도
:class:`~upbit.holdings.HoldingsGuard` 가 보호하는 수량은 건드리지 않는다.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from common.logging import get_logger

from . import indicators as ind
from . import scoring
from .broker import Broker, build_broker
from .client import UpbitClient
from .holdings import HoldingsGuard
from .risk import EquityView, RiskGuard
from .store import UpbitStore, get_store
from .strategy import UpbitConfig, load_config
from .types import Candidate, ExitReason, MarketRegimeView, Position, Regime
from .universe import UniverseBuilder

log = get_logger(__name__)

KST = timezone(timedelta(hours=9))
BTC_MARKET = "KRW-BTC"
# How many top-ranked names pay for the expensive 수급 data pass.
DEEP_SCAN_MULTIPLIER = 3


class TradingEngine:
    def __init__(
        self,
        *,
        config: UpbitConfig | None = None,
        store: UpbitStore | None = None,
        client: UpbitClient | None = None,
        broker: Broker | None = None,
    ) -> None:
        self.store = store or get_store()
        # An explicitly injected config or broker is never swapped out underneath
        # the caller. Only an engine that built its own re-reads the dashboard's
        # edits on reload() — which is what the scheduler wants and tests do not.
        self._config_injected = config is not None
        self.config = config or load_config(self.store)
        self.client = client or UpbitClient()
        self._broker_injected = broker is not None
        self.broker = broker or build_broker(self.client, self.config, self.store)
        self.guard = HoldingsGuard.load(self.store)
        self.risk = RiskGuard(self.config, self.store)
        # One cycle at a time. The scheduler's `max_instances` is per-job, so the
        # 09:10 selection and the 5-minute monitor otherwise overlap on this same
        # instance — reload() would swap the broker mid-scan, and the monitor
        # would close positions while selection sizes against a stale snapshot.
        self._cycle_lock = threading.RLock()

    @contextmanager
    def cycle(self, name: str):
        """Serialise a trading cycle, logging when one had to wait on another."""
        acquired = self._cycle_lock.acquire(blocking=False)
        if not acquired:
            waited = time.monotonic()
            log.info("upbit.cycle.waiting", cycle=name)
            self._cycle_lock.acquire()
            log.info("upbit.cycle.resumed", cycle=name, waited_sec=round(time.monotonic() - waited, 1))
        try:
            yield
        finally:
            self._cycle_lock.release()

    def reload(self) -> None:
        """Pick up config / holdings edits made in the dashboard."""
        if not self._config_injected:
            self.config = load_config(self.store)
        self.guard = HoldingsGuard.load(self.store)
        self.risk = RiskGuard(self.config, self.store)
        if not self._broker_injected:
            self.broker = build_broker(self.client, self.config, self.store)

    @property
    def mode(self) -> str:
        return self.config.mode

    # ==================================================================
    # Account / equity
    # ==================================================================
    def equity_view(self, prices: dict[str, float] | None = None) -> EquityView:
        """Split the account into cash, engine positions and the protected bag."""
        accounts = self.broker.get_accounts()
        tradable, long_term = self.guard.split_balances(accounts)

        cash = 0.0
        coin_rows: list[dict[str, Any]] = []
        for acc in tradable:
            currency = str(acc.get("currency", "")).upper()
            balance = float(acc.get("balance") or 0) + float(acc.get("locked") or 0)
            if currency == "KRW":
                cash += balance
            elif balance > 0:
                coin_rows.append({"symbol": currency, "balance": balance, "raw": acc})

        markets = [f"KRW-{r['symbol']}" for r in coin_rows]
        markets += [f"KRW-{str(a.get('currency', '')).upper()}" for a in long_term]
        prices = prices or self.fetch_prices(sorted(set(markets)))

        open_positions = {p["market"]: p for p in self.store.list_open_positions(self.mode)}
        trading_value = 0.0
        unrealized = 0.0
        rows: list[dict[str, Any]] = []
        for row in coin_rows:
            market = f"KRW-{row['symbol']}"
            price = prices.get(market, 0.0)
            value = row["balance"] * price
            trading_value += value
            pos = open_positions.get(market)
            cost = (pos["volume"] * pos["avg_price"]) if pos else 0.0
            if pos:
                unrealized += pos["volume"] * price - cost
            rows.append(
                {
                    "market": market,
                    "symbol": row["symbol"],
                    "balance": row["balance"],
                    "price": price,
                    "value_krw": value,
                    "avg_price": pos["avg_price"] if pos else float(
                        row["raw"].get("avg_buy_price") or 0
                    ),
                    "cost_krw": cost,
                    "unrealized_pnl": (pos["volume"] * price - cost) if pos else 0.0,
                    "unrealized_pct": ((price / pos["avg_price"] - 1) if pos and pos["avg_price"] else 0.0),
                    "engine_managed": pos is not None,
                    "position_id": pos["id"] if pos else None,
                    "opened_at": pos["opened_at"] if pos else None,
                    "stop_price": pos["stop_price"] if pos else None,
                    "take_price": pos["take_price"] if pos else None,
                }
            )

        longterm_value = 0.0
        for acc in long_term:
            symbol = str(acc.get("currency", "")).upper()
            qty = float(acc.get("_protected_quantity") or 0)
            longterm_value += qty * prices.get(f"KRW-{symbol}", 0.0)

        return EquityView(
            cash_krw=cash,
            trading_value_krw=trading_value,
            longterm_value_krw=longterm_value,
            unrealized_pnl=unrealized,
            positions=rows,
        )

    def fetch_prices(self, markets: list[str]) -> dict[str, float]:
        markets = [m for m in markets if m and m != "KRW-KRW"]
        if not markets:
            return {}
        try:
            return {
                t["market"]: float(t["trade_price"]) for t in self.client.get_tickers(markets)
            }
        except Exception as exc:  # a stale price must not kill a cycle
            log.error("upbit.prices_failed", error=str(exc), n=len(markets))
            return {}

    def long_term_view(self, prices: dict[str, float] | None = None) -> list[dict[str, Any]]:
        """Read-only valuation of the protected bag, for the 포트폴리오 tab."""
        _, long_term = self.guard.split_balances(self.broker.get_accounts())
        markets = [f"KRW-{str(a.get('currency', '')).upper()}" for a in long_term]
        prices = prices or self.fetch_prices(markets)
        out = []
        for acc in long_term:
            symbol = str(acc.get("currency", "")).upper()
            qty = float(acc.get("_protected_quantity") or 0)
            price = prices.get(f"KRW-{symbol}", 0.0)
            avg = float(acc.get("avg_buy_price") or 0)
            out.append(
                {
                    "symbol": symbol,
                    "market": f"KRW-{symbol}",
                    "quantity": qty,
                    "price": price,
                    "value_krw": qty * price,
                    "avg_buy_price": avg,
                    "unrealized_pnl": (price - avg) * qty if avg else 0.0,
                    "unrealized_pct": (price / avg - 1) if avg else 0.0,
                }
            )
        return out

    # ==================================================================
    # Selection cycle
    # ==================================================================
    def run_selection(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Daily scan → score → enter. ``dry_run`` scores and logs but never buys."""
        with self.cycle("selection"):
            return self._run_selection(dry_run=dry_run)

    def _run_selection(self, *, dry_run: bool) -> dict[str, Any]:
        self.reload()
        run_id = self.store.start_run("selection", self.mode, "일일 종목 선정")
        started = time.monotonic()
        log.info("upbit.selection.start", run_id=run_id, mode=self.mode, dry_run=dry_run)

        try:
            regime = self._assess_regime()
            universe = UniverseBuilder(self.client, self.config.universe, self.guard).build()
            candidates = self._score_universe(universe, regime)

            equity = self.equity_view()
            decisions = self._enter_positions(
                candidates, regime, equity, run_id=run_id, dry_run=dry_run
            )

            as_of = datetime.now(KST).isoformat(timespec="seconds")
            self.store.save_analyses(run_id, as_of, candidates)
            self.store.finish_run(
                run_id,
                status="ok",
                regime=regime.regime.value,
                scanned=len(candidates),
                selected=sum(1 for c in candidates if c.selected),
                note=regime.rationale,
                payload={
                    "regime": regime.model_dump(mode="json"),
                    "screened": universe["screened"],
                    "rejected": universe["rejected"][:80],
                    "decisions": decisions,
                    "elapsed_sec": round(time.monotonic() - started, 1),
                    "dry_run": dry_run,
                },
            )
            self.snapshot_equity()

            summary = {
                "run_id": run_id,
                "mode": self.mode,
                "regime": regime.model_dump(mode="json"),
                "scanned": len(candidates),
                "selected": [c.market for c in candidates if c.selected],
                "decisions": decisions,
                "elapsed_sec": round(time.monotonic() - started, 1),
            }
            self.store.log_event(
                "info",
                "selection",
                f"선정 완료 — {len(candidates)}종목 분석, {len(summary['selected'])}종목 진입",
                summary,
            )
            log.info("upbit.selection.done", **{k: summary[k] for k in ("run_id", "scanned")})
            return summary

        except Exception as exc:  # a failed scan must be visible, not fatal
            log.error("upbit.selection.failed", run_id=run_id, error=str(exc))
            self.store.finish_run(run_id, status="error", note=str(exc))
            self.store.log_event("error", "selection", f"선정 실패: {exc}")
            return {"run_id": run_id, "error": str(exc)}

    # ------------------------------------------------------------------
    def _assess_regime(self) -> MarketRegimeView:
        candles = self.client.get_day_candles(BTC_MARKET, self.config.scoring.candle_count)
        regime = scoring.assess_regime(candles)
        self._btc_df = ind.candles_to_frame(candles)
        return regime

    def _score_universe(
        self, universe: dict[str, Any], regime: MarketRegimeView
    ) -> list[Candidate]:
        """Two-stage scan: cheap pillars for everyone, 수급 for the shortlist."""
        cfg = self.config.scoring
        weights = self.config.scoring.weights.normalized()
        btc_df: pd.DataFrame = getattr(self, "_btc_df", pd.DataFrame())
        names = universe["korean_names"]

        # --- stage 1: daily candles only
        stage1: list[tuple[Candidate, list[dict[str, Any]], dict[str, Any]]] = []
        for ticker in universe["tickers"]:
            market = ticker["market"]
            try:
                candles = self.client.get_day_candles(market, cfg.candle_count)
            except Exception as exc:  # skip the name, keep the scan
                log.warning("upbit.candles_failed", market=market, error=str(exc))
                continue
            cand = scoring.score_candidate(
                market=market,
                ticker=ticker,
                candles=candles,
                btc_df=btc_df,
                regime=regime,
                korean_name=names.get(market),
                weights=weights,
            )
            stage1.append((cand, candles, ticker))

        stage1.sort(key=lambda item: item[0].score.total, reverse=True)
        deep_n = min(
            len(stage1), max(self.config.strategy.max_positions * DEEP_SCAN_MULTIPLIER, 10)
        )
        shortlist = stage1[:deep_n]

        # --- stage 2: orderbook (batched) + ticks + intraday for the shortlist
        orderbooks: dict[str, dict[str, Any]] = {}
        shortlist_markets = [c.market for c, _, _ in shortlist]
        for i in range(0, len(shortlist_markets), 10):
            chunk = shortlist_markets[i : i + 10]
            try:
                for book in self.client.get_orderbook(chunk):
                    orderbooks[book["market"]] = book
            except Exception as exc:  # degrade to the stage-1 score
                log.warning("upbit.orderbook_failed", markets=chunk, error=str(exc))

        rescored: list[Candidate] = []
        for cand, candles, ticker in shortlist:
            market = cand.market
            ticks = intraday = None
            try:
                ticks = self.client.get_trade_ticks(market, cfg.tick_count)
                intraday = self.client.get_minute_candles(
                    market, cfg.intraday_unit, cfg.intraday_count
                )
            except Exception as exc:  # degrade to the stage-1 score
                log.warning("upbit.deepscan_failed", market=market, error=str(exc))
            rescored.append(
                scoring.score_candidate(
                    market=market,
                    ticker=ticker,
                    candles=candles,
                    btc_df=btc_df,
                    regime=regime,
                    orderbook=orderbooks.get(market),
                    ticks=ticks,
                    intraday=intraday,
                    korean_name=names.get(market),
                    weights=weights,
                )
            )

        deep_markets = {c.market for c in rescored}
        candidates = rescored + [c for c, _, _ in stage1 if c.market not in deep_markets]
        candidates.sort(key=lambda c: c.score.total, reverse=True)
        for rank, cand in enumerate(candidates, start=1):
            cand.rank = rank
        return candidates

    # ------------------------------------------------------------------
    def _enter_positions(
        self,
        candidates: list[Candidate],
        regime: MarketRegimeView,
        equity: EquityView,
        *,
        run_id: int,
        dry_run: bool,
    ) -> list[dict[str, Any]]:
        """Walk the ranked list and buy while the guards keep saying yes."""
        decisions: list[dict[str, Any]] = []
        open_positions = self.store.list_open_positions(self.mode)
        held = {p["market"] for p in open_positions}

        halted, loss_pct = self.risk.daily_loss_breached(equity)
        if halted:
            note = f"일일 손실 한도 초과 ({loss_pct * 100:.2f}%) — 신규 진입 중단"
            self.store.log_event("warning", "risk", note)
            return [{"market": "-", "action": "halt", "reason": note}]

        remaining = self.risk.available_capital(equity, regime)
        n_open = len(open_positions)

        # Explain an entirely empty run rather than returning a silent [].
        if n_open >= self.config.strategy.max_positions:
            note = (
                f"이미 최대 보유 종목 수({self.config.strategy.max_positions})를 채웠습니다 "
                f"— 청산 후 다음 사이클에 진입합니다."
            )
            self.store.log_event("info", "selection", note)
            return [{"market": "-", "action": "skip", "reason": note}]
        if remaining <= 0:
            note = (
                f"가용 자금이 없습니다 (노출 {equity.exposure_pct * 100:.1f}%, "
                f"현금 {equity.cash_krw:,.0f} KRW) — 노출·현금 버퍼 한도에 걸렸습니다."
            )
            self.store.log_event("info", "selection", note)
            return [{"market": "-", "action": "skip", "reason": note}]

        for cand in candidates:
            if n_open >= self.config.strategy.max_positions:
                break
            if cand.market in held:
                decisions.append(
                    {"market": cand.market, "action": "skip", "reason": "이미 보유 중"}
                )
                continue
            if self.guard.is_protected(cand.symbol):
                # Belt-and-braces: the universe screen already dropped these.
                decisions.append(
                    {"market": cand.market, "action": "skip", "reason": "장기보유 코인"}
                )
                continue

            sizing = self.risk.size_position(
                equity=equity,
                regime=regime,
                score=cand.score.total,
                open_positions=n_open,
                remaining_capital=remaining,
            )
            if not sizing.approved:
                decisions.append(
                    {
                        "market": cand.market,
                        "action": "reject",
                        "score": cand.score.total,
                        "reason": sizing.reason,
                    }
                )
                # A score below the floor means every later candidate fails too.
                if "종합점수" in sizing.reason:
                    break
                continue

            if dry_run:
                cand.selected = True
                decisions.append(
                    {
                        "market": cand.market,
                        "action": "dry_run_buy",
                        "score": cand.score.total,
                        "krw": sizing.krw_amount,
                        "reason": "시뮬레이션 — 주문 미실행",
                    }
                )
                n_open += 1
                remaining -= sizing.krw_amount
                continue

            outcome = self._open_position(cand, sizing.krw_amount, run_id=run_id)
            decisions.append(outcome)
            if outcome["action"] == "buy":
                cand.selected = True
                n_open += 1
                remaining -= sizing.krw_amount

        return decisions

    def _open_position(
        self, cand: Candidate, krw_amount: float, *, run_id: int
    ) -> dict[str, Any]:
        reason = f"종합점수 {cand.score.total:.1f} · {cand.reason}"
        result = self.broker.buy_market(cand.market, krw_amount, reason=reason)

        if result.state == "rejected" or result.executed_volume <= 0:
            self.store.record_trade(
                run_id=run_id,
                mode=self.mode,
                market=cand.market,
                symbol=cand.symbol,
                side="bid",
                ord_type=result.request.ord_type,
                volume=0,
                price=0,
                krw_amount=krw_amount,
                fee=0,
                state="rejected",
                uuid=result.uuid,
                reason=reason,
                score=cand.score.total,
                message=result.message,
            )
            self.store.log_event(
                "error", "order", f"{cand.market} 매수 실패: {result.message}"
            )
            return {"market": cand.market, "action": "failed", "reason": result.message}

        cost_per_unit = result.krw_amount / result.executed_volume
        stop, take = self.risk.exit_levels(cost_per_unit)

        trade_id = self.store.record_trade(
            run_id=run_id,
            mode=self.mode,
            market=cand.market,
            symbol=cand.symbol,
            side="bid",
            ord_type=result.request.ord_type,
            volume=result.executed_volume,
            price=result.avg_price or cost_per_unit,
            krw_amount=result.krw_amount,
            fee=result.paid_fee,
            state=result.state,
            uuid=result.uuid,
            reason=reason,
            score=cand.score.total,
            message=result.message,
        )
        position = Position(
            market=cand.market,
            symbol=cand.symbol,
            volume=result.executed_volume,
            avg_price=cost_per_unit,
            opened_at=datetime.now(KST),
            high_water=result.avg_price or cost_per_unit,
            stop_price=stop,
            take_price=take,
            mode=self.mode,
            run_id=run_id,
        )
        self.store.open_position(position, entry_trade_id=trade_id, score=cand.score.total)
        self.store.log_event(
            "info",
            "order",
            f"{cand.market} 매수 {result.krw_amount:,.0f} KRW "
            f"@ {cost_per_unit:,.4f} (점수 {cand.score.total:.1f})",
        )
        return {
            "market": cand.market,
            "action": "buy",
            "score": cand.score.total,
            "krw": result.krw_amount,
            "price": cost_per_unit,
            "volume": result.executed_volume,
            "reason": reason,
        }

    # ==================================================================
    # Monitoring / exits
    # ==================================================================
    def monitor_positions(self, *, force_exit: bool = False) -> dict[str, Any]:
        """Re-price every open position and close whichever rules have tripped."""
        with self.cycle("monitor"):
            return self._monitor_positions(force_exit=force_exit)

    def _monitor_positions(self, *, force_exit: bool) -> dict[str, Any]:
        self.reload()
        positions = self.store.list_open_positions(self.mode)
        if not positions:
            return {"checked": 0, "closed": []}

        run_id = self.store.start_run("monitor", self.mode, "포지션 모니터링")
        prices = self.fetch_prices([p["market"] for p in positions])
        regime = self._safe_regime()
        closed: list[dict[str, Any]] = []
        updates = 0

        for pos in positions:
            price = prices.get(pos["market"], 0.0)
            if price <= 0:
                continue

            high_water = max(float(pos["high_water"] or 0), price)
            if high_water > float(pos["high_water"] or 0):
                self.store.update_position(pos["id"], high_water=high_water)
                updates += 1

            reason = self._exit_reason(pos, price, high_water, regime, force_exit)
            if reason is None:
                continue
            result = self._close_position(pos, reason, run_id=run_id, price=price)
            if result:
                closed.append(result)

        self.store.finish_run(
            run_id,
            status="ok",
            regime=regime.regime.value,
            scanned=len(positions),
            selected=len(closed),
            note=f"{len(closed)}건 청산",
            payload={"closed": closed, "price_updates": updates},
        )
        if closed:
            self.store.log_event("info", "monitor", f"{len(closed)}건 청산 완료", {"closed": closed})
        return {"checked": len(positions), "closed": closed, "regime": regime.regime.value}

    def _safe_regime(self) -> MarketRegimeView:
        try:
            return scoring.assess_regime(self.client.get_day_candles(BTC_MARKET, 90))
        except Exception as exc:  # never block exits on a data hiccup
            log.warning("upbit.regime_failed", error=str(exc))
            return MarketRegimeView(rationale="BTC 데이터 조회 실패 — 중립 처리")

    def _exit_reason(
        self,
        pos: dict[str, Any],
        price: float,
        high_water: float,
        regime: MarketRegimeView,
        force_exit: bool,
    ) -> ExitReason | None:
        if force_exit:
            return ExitReason.TIME_EXIT

        entry = float(pos["avg_price"])
        if price <= float(pos["stop_price"] or 0):
            return ExitReason.STOP_LOSS
        if pos["take_price"] and price >= float(pos["take_price"]):
            return ExitReason.TAKE_PROFIT

        trailing = self.risk.trailing_stop(entry, high_water)
        if trailing is not None and price <= trailing:
            return ExitReason.TRAILING_STOP

        opened = _parse_dt(pos["opened_at"])
        if opened is not None:
            age_h = (datetime.now(KST) - opened).total_seconds() / 3600
            if age_h >= self.config.strategy.max_hold_hours:
                return ExitReason.TIME_EXIT

        if regime.regime is Regime.RISK_OFF:
            return ExitReason.REGIME_EXIT
        return None

    def _close_position(
        self,
        pos: dict[str, Any],
        reason: ExitReason,
        *,
        run_id: int | None,
        price: float | None = None,
    ) -> dict[str, Any] | None:
        market, symbol = pos["market"], pos["symbol"]

        # Never sell into the protected bag: cap the order at what the engine owns.
        on_exchange = self.broker.coin_balance(symbol)
        sellable = min(float(pos["volume"]), self.guard.tradable_quantity(symbol, on_exchange))
        if sellable <= 0:
            # The engine can no longer act on these coins — either the user moved
            # the symbol into 장기보유 after the entry, or the balance left the
            # exchange. Release the position instead of retrying forever: the
            # coins stay put, and the slot stops counting against max_positions.
            return self._release_position(pos, on_exchange)

        result = self.broker.sell_market(market, sellable, reason=reason.value)
        if result.state == "rejected" or result.executed_volume <= 0:
            self.store.record_trade(
                run_id=run_id,
                mode=self.mode,
                market=market,
                symbol=symbol,
                side="ask",
                ord_type=result.request.ord_type,
                volume=0,
                price=price or 0,
                krw_amount=0,
                fee=0,
                state="rejected",
                uuid=result.uuid,
                reason=reason.value,
                message=result.message,
            )
            self.store.log_event("error", "order", f"{market} 매도 실패: {result.message}")
            return None

        cost = result.executed_volume * float(pos["avg_price"])
        pnl = result.krw_amount - cost
        pnl_pct = (pnl / cost) if cost > 0 else 0.0

        self.store.record_trade(
            run_id=run_id,
            mode=self.mode,
            market=market,
            symbol=symbol,
            side="ask",
            ord_type=result.request.ord_type,
            volume=result.executed_volume,
            price=result.avg_price,
            krw_amount=result.krw_amount,
            fee=result.paid_fee,
            state=result.state,
            uuid=result.uuid,
            reason=reason.value,
            pnl=pnl,
            pnl_pct=pnl_pct,
            entry_trade_id=pos.get("entry_trade_id"),
            message=result.message,
        )

        remaining = float(pos["volume"]) - result.executed_volume
        if remaining > 1e-10:
            # Partial fill: keep the position open with what is left.
            self.store.update_position(pos["id"], volume=remaining)
            self.store.log_event(
                "warning", "order", f"{market} 부분 청산 — 잔여 {remaining:.8f}"
            )
        else:
            self.store.close_position(pos["id"], exit_reason=reason.value, realized_pnl=pnl)

        self.store.log_event(
            "info",
            "order",
            f"{market} 매도 [{reason.value}] {pnl:+,.0f} KRW ({pnl_pct * 100:+.2f}%)",
        )
        return {
            "market": market,
            "reason": reason.value,
            "volume": result.executed_volume,
            "price": result.avg_price,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
        }

    def _release_position(self, pos: dict[str, Any], on_exchange: float) -> dict[str, Any]:
        """Stop managing a position without selling anything."""
        market, symbol = pos["market"], pos["symbol"]
        protected = self.guard.is_protected(symbol)
        reason = ExitReason.LONG_TERM_PROTECTED if protected else ExitReason.BALANCE_MISSING
        note = (
            f"{market} 자동매매 관리 해제 — 장기보유로 등록되어 매도하지 않습니다."
            if protected
            else f"{market} 자동매매 관리 해제 — 거래소 잔고 없음 (보유 {on_exchange:.8f})."
        )
        self.store.close_position(pos["id"], exit_reason=reason.value, realized_pnl=0.0)
        self.store.log_event("warning", "order", note)
        log.warning("upbit.position_released", market=market, reason=reason.value)
        return {
            "market": market,
            "reason": reason.value,
            "released": True,
            "volume": float(pos["volume"]),
            "price": 0.0,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "note": note,
        }

    # ==================================================================
    # Reconciliation
    # ==================================================================
    def reconcile(self) -> dict[str, Any]:
        """Re-align stored positions with what the exchange actually holds.

        A crash between placing an order and writing the row, or a manual sell in
        the Upbit app, leaves the journal claiming coins that are not there. The
        engine would then keep trying to exit a phantom position on every cycle.

        Positions are only ever shrunk to the real balance or released — never
        grown — so this can never invent exposure the user did not have.
        """
        with self.cycle("reconcile"):
            self.reload()
            positions = self.store.list_open_positions(self.mode)
            if not positions:
                return {"checked": 0, "released": [], "adjusted": []}

            released: list[dict[str, Any]] = []
            adjusted: list[dict[str, Any]] = []
            for pos in positions:
                symbol = pos["symbol"]
                try:
                    on_exchange = self.broker.coin_balance(symbol)
                except Exception as exc:  # a lookup failure must not drop a position
                    log.error("upbit.reconcile.balance_failed", symbol=symbol, error=str(exc))
                    continue

                tradable = self.guard.tradable_quantity(symbol, on_exchange)
                recorded = float(pos["volume"])

                # Below the exchange's dust threshold there is nothing left to sell.
                if tradable <= recorded * 1e-6:
                    self._release_position(pos, on_exchange)
                    released.append({"market": pos["market"], "recorded": recorded})
                    continue

                if tradable < recorded * 0.999:
                    self.store.update_position(pos["id"], volume=tradable)
                    self.store.log_event(
                        "warning",
                        "reconcile",
                        f"{pos['market']} 수량 보정 {recorded:.8f} → {tradable:.8f} "
                        f"(거래소 잔고 기준)",
                    )
                    adjusted.append(
                        {"market": pos["market"], "from": recorded, "to": tradable}
                    )

            summary = {"checked": len(positions), "released": released, "adjusted": adjusted}
            if released or adjusted:
                self.store.log_event(
                    "warning",
                    "reconcile",
                    f"기동 정합성 점검 — 관리해제 {len(released)}건, 수량보정 {len(adjusted)}건",
                    summary,
                )
                log.warning("upbit.reconcile.drift", **{k: len(v) for k, v in summary.items() if isinstance(v, list)})
            else:
                log.info("upbit.reconcile.clean", checked=len(positions))
            return summary

    # ==================================================================
    # Manual controls
    # ==================================================================
    def close_position_by_id(self, position_id: int, reason: str = "manual") -> dict[str, Any]:
        with self.cycle("manual_close"):
            return self._close_position_by_id(position_id, reason)

    def _close_position_by_id(self, position_id: int, reason: str) -> dict[str, Any]:
        positions = [p for p in self.store.list_open_positions(self.mode) if p["id"] == position_id]
        if not positions:
            return {"error": "해당 포지션을 찾을 수 없습니다."}
        try:
            exit_reason = ExitReason(reason)
        except ValueError:
            exit_reason = ExitReason.MANUAL
        run_id = self.store.start_run("manual", self.mode, f"수동 청산 #{position_id}")
        result = self._close_position(positions[0], exit_reason, run_id=run_id)
        self.store.finish_run(run_id, status="ok", note="수동 청산", payload=result or {})
        return result or {"error": "청산에 실패했습니다."}

    def liquidate_all(self, reason: str = "panic") -> dict[str, Any]:
        """Kill switch — flatten every engine position. 장기보유 코인은 그대로 둔다."""
        with self.cycle("liquidate"):
            return self._liquidate_all(reason=reason)

    def _liquidate_all(self, reason: str) -> dict[str, Any]:
        self.reload()
        positions = self.store.list_open_positions(self.mode)
        run_id = self.store.start_run("liquidate", self.mode, f"전량 청산 ({reason})")
        self.store.log_event("warning", "control", f"전량 청산 실행: {reason}")

        try:
            exit_reason = ExitReason(reason)
        except ValueError:
            exit_reason = ExitReason.PANIC

        closed = []
        for pos in positions:
            result = self._close_position(pos, exit_reason, run_id=run_id)
            if result:
                closed.append(result)

        self.store.finish_run(
            run_id, status="ok", scanned=len(positions), selected=len(closed),
            note=f"{len(closed)}/{len(positions)}건 청산", payload={"closed": closed},
        )
        self.snapshot_equity()
        return {
            "requested": len(positions),
            "closed": closed,
            "protected_symbols": sorted(self.guard.symbols),
        }

    def snapshot_equity(self) -> dict[str, Any]:
        """Sample the equity curve — drives the dashboard's 자산 추이 chart."""
        equity = self.equity_view()
        realized = self.store.trade_stats(mode=self.mode)["total_pnl"]
        self.store.snapshot_equity(
            mode=self.mode,
            total_krw=equity.total_equity,
            cash_krw=equity.cash_krw,
            trading_krw=equity.trading_value_krw,
            longterm_krw=equity.longterm_value_krw,
            realized_pnl=realized,
            unrealized_pnl=equity.unrealized_pnl,
        )
        return {
            "total_krw": equity.total_equity,
            "cash_krw": equity.cash_krw,
            "trading_krw": equity.trading_value_krw,
            "longterm_krw": equity.longterm_value_krw,
            "unrealized_pnl": equity.unrealized_pnl,
            "realized_pnl": realized,
        }


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt
