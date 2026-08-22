"""Order execution — one interface, two backends.

``PaperBroker`` keeps a virtual KRW/coin ledger in ``app_settings`` and fills at
the live public price plus a fee and a slippage assumption. It is the default,
and the only backend tests are ever allowed to touch.

``LiveBroker`` forwards to the real Upbit exchange API. It is reachable only
when the user has explicitly flipped the mode switch in the dashboard *and*
registered a key pair; nothing constructs it implicitly.

Upbit KRW conventions baked in here:
    * 시장가 매수 → ``ord_type="price"`` with ``price`` = the KRW amount to spend
    * 시장가 매도 → ``ord_type="market"`` with ``volume`` = the units to sell
    * minimum order value is 5,000 KRW
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from common.logging import get_logger

from .client import UpbitClient, round_to_tick
from .store import UpbitStore, get_store
from .types import OrderRequest, OrderResult

log = get_logger(__name__)

MIN_ORDER_KRW = 5_000.0
PAPER_ACCOUNT_KEY = "paper_account"
# Market orders on thin alt books do not fill at the touch; charge for it.
PAPER_SLIPPAGE = 0.0015


class Broker(ABC):
    mode: str

    @abstractmethod
    def get_accounts(self) -> list[dict[str, Any]]:
        """Balances in Upbit's `/v1/accounts` shape."""

    @abstractmethod
    def buy_market(self, market: str, krw_amount: float, *, reason: str = "") -> OrderResult: ...

    @abstractmethod
    def sell_market(self, market: str, volume: float, *, reason: str = "") -> OrderResult: ...

    def krw_balance(self) -> float:
        for acc in self.get_accounts():
            if str(acc.get("currency", "")).upper() == "KRW":
                return float(acc.get("balance") or 0)
        return 0.0

    def coin_balance(self, symbol: str) -> float:
        symbol = symbol.split("-")[-1].upper()
        for acc in self.get_accounts():
            if str(acc.get("currency", "")).upper() == symbol:
                return float(acc.get("balance") or 0)
        return 0.0


# ----------------------------------------------------------------------
# Paper
# ----------------------------------------------------------------------
class PaperBroker(Broker):
    """Simulated exchange backed by live prices — never touches real money."""

    mode = "paper"

    def __init__(
        self,
        client: UpbitClient,
        store: UpbitStore | None = None,
        *,
        fee_rate: float = 0.0005,
        initial_krw: float = 10_000_000,
    ) -> None:
        self.client = client
        self.store = store or get_store()
        self.fee_rate = fee_rate
        self.initial_krw = initial_krw
        self._ensure_account()

    # -- ledger ---------------------------------------------------------
    def _ensure_account(self) -> dict[str, Any]:
        state = self.store.get_override(PAPER_ACCOUNT_KEY)
        if not state:
            state = {"krw": self.initial_krw, "coins": {}, "deposits": self.initial_krw}
            self.store.set_override(PAPER_ACCOUNT_KEY, state)
            self.store.log_event(
                "info", "paper", f"모의 계좌를 {self.initial_krw:,.0f} KRW 로 개설했습니다."
            )
        return state

    def _state(self) -> dict[str, Any]:
        return self._ensure_account()

    def _save(self, state: dict[str, Any]) -> None:
        self.store.set_override(PAPER_ACCOUNT_KEY, state)

    def reset(self, initial_krw: float | None = None) -> dict[str, Any]:
        amount = initial_krw if initial_krw is not None else self.initial_krw
        state = {"krw": amount, "coins": {}, "deposits": amount}
        self._save(state)
        self.store.log_event("warning", "paper", f"모의 계좌를 {amount:,.0f} KRW 로 초기화했습니다.")
        return state

    def deposit(self, krw: float) -> dict[str, Any]:
        state = self._state()
        state["krw"] = float(state["krw"]) + krw
        state["deposits"] = float(state.get("deposits", 0)) + krw
        self._save(state)
        return state

    # -- Broker interface ----------------------------------------------
    def get_accounts(self) -> list[dict[str, Any]]:
        state = self._state()
        out = [
            {
                "currency": "KRW",
                "balance": str(state["krw"]),
                "locked": "0",
                "avg_buy_price": "0",
                "unit_currency": "KRW",
            }
        ]
        for symbol, holding in (state.get("coins") or {}).items():
            if float(holding.get("volume") or 0) <= 0:
                continue
            out.append(
                {
                    "currency": symbol,
                    "balance": str(holding["volume"]),
                    "locked": "0",
                    "avg_buy_price": str(holding.get("avg_price") or 0),
                    "unit_currency": "KRW",
                }
            )
        return out

    def _last_price(self, market: str) -> float:
        ticker = self.client.get_tickers([market])
        if not ticker:
            raise RuntimeError(f"{market} 현재가를 가져오지 못했습니다.")
        return float(ticker[0]["trade_price"])

    def buy_market(self, market: str, krw_amount: float, *, reason: str = "") -> OrderResult:
        req = OrderRequest(
            market=market, side="bid", ord_type="price", price=krw_amount, reason=reason
        )
        state = self._state()
        if krw_amount < MIN_ORDER_KRW:
            return OrderResult(
                request=req, state="rejected", message=f"최소 주문금액 {MIN_ORDER_KRW:,.0f} KRW 미만"
            )
        if float(state["krw"]) < krw_amount:
            return OrderResult(request=req, state="rejected", message="모의 계좌 KRW 잔고 부족")

        price = self._last_price(market) * (1 + PAPER_SLIPPAGE)
        fee = krw_amount * self.fee_rate
        volume = (krw_amount - fee) / price
        symbol = market.split("-")[-1]

        coins = state.setdefault("coins", {})
        holding = coins.get(symbol, {"volume": 0.0, "avg_price": 0.0})
        prev_vol = float(holding["volume"])
        prev_cost = prev_vol * float(holding["avg_price"])
        new_vol = prev_vol + volume
        coins[symbol] = {
            "volume": new_vol,
            "avg_price": (prev_cost + volume * price) / new_vol if new_vol > 0 else 0.0,
        }
        state["krw"] = float(state["krw"]) - krw_amount
        self._save(state)

        return OrderResult(
            request=req,
            uuid=f"paper-{datetime.utcnow().timestamp():.0f}",
            state="simulated",
            executed_volume=volume,
            avg_price=price,
            paid_fee=fee,
            krw_amount=krw_amount,
            message="모의 매수 체결",
        )

    def sell_market(self, market: str, volume: float, *, reason: str = "") -> OrderResult:
        req = OrderRequest(
            market=market, side="ask", ord_type="market", volume=volume, reason=reason
        )
        state = self._state()
        symbol = market.split("-")[-1]
        holding = (state.get("coins") or {}).get(symbol)
        if not holding or float(holding["volume"]) <= 0:
            return OrderResult(request=req, state="rejected", message="모의 계좌 보유 수량 없음")

        volume = min(volume, float(holding["volume"]))
        price = self._last_price(market) * (1 - PAPER_SLIPPAGE)
        gross = volume * price
        if gross < MIN_ORDER_KRW:
            return OrderResult(
                request=req, state="rejected", message=f"최소 주문금액 {MIN_ORDER_KRW:,.0f} KRW 미만"
            )
        fee = gross * self.fee_rate

        remaining = float(holding["volume"]) - volume
        if remaining <= 1e-12:
            state["coins"].pop(symbol, None)
        else:
            state["coins"][symbol] = {"volume": remaining, "avg_price": holding["avg_price"]}
        state["krw"] = float(state["krw"]) + gross - fee
        self._save(state)

        return OrderResult(
            request=req,
            uuid=f"paper-{datetime.utcnow().timestamp():.0f}",
            state="simulated",
            executed_volume=volume,
            avg_price=price,
            paid_fee=fee,
            krw_amount=gross - fee,
            message="모의 매도 체결",
        )


# ----------------------------------------------------------------------
# Live
# ----------------------------------------------------------------------
class LiveBroker(Broker):
    """Real orders on Upbit. Only constructed when mode == 'live'."""

    mode = "live"

    def __init__(
        self,
        client: UpbitClient,
        store: UpbitStore | None = None,
        *,
        fee_rate: float = 0.0005,
        order_style: str = "market",
        limit_offset_bps: float = 10.0,
        fill_timeout: float = 20.0,
    ) -> None:
        if not client.has_credentials():
            raise RuntimeError("실거래 모드에는 업비트 API 키가 필요합니다.")
        self.client = client
        self.store = store or get_store()
        self.fee_rate = fee_rate
        self.order_style = order_style
        self.limit_offset_bps = limit_offset_bps
        self.fill_timeout = fill_timeout

    def get_accounts(self) -> list[dict[str, Any]]:
        return self.client.get_accounts()

    def _last_price(self, market: str) -> float:
        ticker = self.client.get_tickers([market])
        if not ticker:
            raise RuntimeError(f"{market} 현재가를 가져오지 못했습니다.")
        return float(ticker[0]["trade_price"])

    def buy_market(self, market: str, krw_amount: float, *, reason: str = "") -> OrderResult:
        if krw_amount < MIN_ORDER_KRW:
            req = OrderRequest(market=market, side="bid", ord_type="price", price=krw_amount)
            return OrderResult(
                request=req, state="rejected", message=f"최소 주문금액 {MIN_ORDER_KRW:,.0f} KRW 미만"
            )

        if self.order_style == "limit":
            # Cross the spread so a day-trade entry actually fills.
            price = round_to_tick(
                self._last_price(market) * (1 + self.limit_offset_bps / 10_000), up=True
            )
            volume = (krw_amount / price) if price > 0 else 0.0
            req = OrderRequest(
                market=market,
                side="bid",
                ord_type="limit",
                price=price,
                volume=volume,
                reason=reason,
            )
        else:
            req = OrderRequest(
                market=market, side="bid", ord_type="price", price=krw_amount, reason=reason
            )

        result = self.client.place_order(req)
        return self._settle(result, fallback_krw=krw_amount)

    def sell_market(self, market: str, volume: float, *, reason: str = "") -> OrderResult:
        if self.order_style == "limit":
            price = round_to_tick(self._last_price(market) * (1 - self.limit_offset_bps / 10_000))
            req = OrderRequest(
                market=market,
                side="ask",
                ord_type="limit",
                price=price,
                volume=volume,
                reason=reason,
            )
        else:
            req = OrderRequest(
                market=market, side="ask", ord_type="market", volume=volume, reason=reason
            )

        result = self.client.place_order(req)
        return self._settle(result)

    def _settle(self, result: OrderResult, *, fallback_krw: float = 0.0) -> OrderResult:
        """Poll the order to a terminal state and fill in the realised numbers."""
        if result.state == "rejected" or not result.uuid:
            return result
        try:
            raw = self.client.wait_for_fill(result.uuid, timeout=self.fill_timeout)
        except Exception as exc:  # the order exists; reporting is best-effort
            log.error("upbit.order_poll_failed", uuid=result.uuid, error=str(exc))
            result.message = f"체결 조회 실패: {exc}"
            return result

        trades = raw.get("trades") or []
        executed = float(raw.get("executed_volume") or 0)
        funds = sum(float(t.get("funds") or 0) for t in trades)
        if not funds:
            funds = float(raw.get("executed_funds") or 0) or fallback_krw
        fee = float(raw.get("paid_fee") or 0)

        result.raw = raw
        result.state = "done" if raw.get("state") == "done" else "submitted"
        result.executed_volume = executed
        result.avg_price = (funds / executed) if executed > 0 else 0.0
        result.paid_fee = fee
        # `krw_amount` is the cash actually moved: debited on a buy, credited on
        # a sell. The engine derives cost basis and P&L straight from it, so both
        # brokers must report it the same way.
        result.krw_amount = funds + fee if result.request.side == "bid" else funds - fee
        result.message = raw.get("state", "")
        return result


def build_broker(client: UpbitClient, config: Any, store: UpbitStore | None = None) -> Broker:
    """Pick a backend from the effective config. Defaults to paper on any doubt."""
    store = store or get_store()
    if config.mode == "live":
        return LiveBroker(
            client,
            store,
            fee_rate=config.fee_rate,
            order_style=config.strategy.order_style,
            limit_offset_bps=config.strategy.limit_offset_bps,
        )
    return PaperBroker(
        client, store, fee_rate=config.fee_rate, initial_krw=config.paper_initial_krw
    )
