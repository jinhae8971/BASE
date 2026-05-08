"""KIS realtime websocket — H0STCNT0 (체결가) subscriber.

Subscribes to the per-tick price stream for every name we currently hold
(re-evaluated whenever account state changes) and fires ``stops_callback``
on every tick. The callback is intentionally cheap — it just updates the
in-process price cache and triggers ``intraday_stops_phase`` when *any*
position moves more than ``websocket.tick_threshold`` (default 1.5%) since
the last evaluation.

Reference: https://apiportal.koreainvestment.com/intro

Async because KIS publishes ~one message per tick during regular hours.
The runner is intentionally a separate process from the APScheduler block
so a websocket stall never affects the cron jobs.

Run standalone:

    python -m broker.kis_websocket

Inside Docker compose: see the optional ``websocket`` service.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from common.config import get_env, get_setting
from common.logging import get_logger, setup_logging
from common.metrics import GUARD_FIRES
from common.notifications import notify_warning

log = get_logger(__name__)

WS_URLS = {
    "paper": "ws://ops.koreainvestment.com:31000",
    "live": "ws://ops.koreainvestment.com:21000",
}


class RealtimeStops:
    """Maintains a price cache + fires stops on big intra-tick moves.

    Caller plumbing:
        stops = RealtimeStops()
        stops.subscribe(["005930", "000660"])
        await stops.run()   # blocks
    """

    def __init__(self) -> None:
        env = get_env()
        self.app_key = env.kis_app_key
        self.app_secret = env.kis_app_secret
        self.kis_env = (env.kis_env or "paper").lower()
        self.url = WS_URLS.get(self.kis_env, WS_URLS["paper"])
        self.threshold = float(
            get_setting("websocket.tick_threshold", 0.015)
        )  # 1.5% one-tick move triggers stops eval
        self.cooldown_s = int(get_setting("websocket.cooldown_seconds", 60))
        self._last_eval: dict[str, float] = {}     # ticker -> last eval time
        self._reference: dict[str, float] = {}     # ticker -> last reference price
        self._tickers: set[str] = set()

    # ------------------------------------------------------------------
    def subscribe(self, tickers: list[str]) -> None:
        """Add tickers to the subscription set."""
        self._tickers.update(tickers)

    # ------------------------------------------------------------------
    async def _build_subscribe_message(self, ticker: str) -> str:
        """KIS realtime subscription envelope."""
        return json.dumps(
            {
                "header": {
                    "approval_key": await self._approval_key(),
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {
                    "input": {"tr_id": "H0STCNT0", "tr_key": ticker},
                },
            }
        )

    async def _approval_key(self) -> str:
        """OAuth approval_key required for the websocket handshake.

        KIS exposes a separate ``/oauth2/Approval`` endpoint that mints a
        websocket-specific token from the same app_key/app_secret.
        """
        import httpx

        from .kis_client import BASE_URLS

        url = f"{BASE_URLS[self.kis_env]}/oauth2/Approval"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            return r.json()["approval_key"]

    # ------------------------------------------------------------------
    def handle_tick(self, ticker: str, price: float) -> None:
        """Synchronous tick handler — update reference + maybe fire stops.

        Idempotent: re-firing within ``cooldown_s`` is suppressed.
        """
        now = time.monotonic()
        ref = self._reference.get(ticker)
        if ref is None or ref <= 0:
            self._reference[ticker] = price
            return

        delta = price / ref - 1.0
        if abs(delta) < self.threshold:
            return

        last = self._last_eval.get(ticker, 0)
        if now - last < self.cooldown_s:
            return

        self._last_eval[ticker] = now
        self._reference[ticker] = price
        log.warning(
            "websocket.tick_breach",
            ticker=ticker,
            delta=round(delta, 4),
            threshold=self.threshold,
        )
        GUARD_FIRES.labels(guard="websocket_tick").inc()
        # Spawn the stops phase synchronously — it's only ~hundreds of ms
        # and we don't want to drop the websocket loop blocking on async.
        try:
            from scheduler.daily_pipeline import intraday_stops_phase

            intraday_stops_phase()
        except Exception as e:
            log.error("websocket.stops_run_failed", error=str(e))
            notify_warning(
                "Websocket stop trigger failed",
                str(e),
                ticker=ticker,
                delta=f"{delta:+.2%}",
            )

    # ------------------------------------------------------------------
    async def run(self) -> None:
        """Connect, subscribe to every queued ticker, dispatch ticks."""
        try:
            import websockets  # type: ignore
        except Exception as e:
            log.error("websocket.dependency_missing", error=str(e))
            return

        if not self._tickers:
            log.warning("websocket.no_tickers_subscribed")
            return

        log.info("websocket.connecting", url=self.url, n=len(self._tickers))
        async with websockets.connect(self.url, ping_interval=30) as ws:
            for tkr in sorted(self._tickers):
                await ws.send(await self._build_subscribe_message(tkr))
            await self._receive_loop(ws)

    async def _receive_loop(self, ws: Any) -> None:
        async for raw in ws:
            try:
                self._dispatch(raw)
            except Exception as e:
                log.warning("websocket.dispatch_failed", error=str(e))

    def _dispatch(self, raw: str) -> None:
        """Parse a KIS H0STCNT0 frame and fire ``handle_tick``.

        Frame format: ``0|H0STCNT0|001|005930^TIME^PRICE^...``. Pipe-delimited
        header, ``^``-delimited payload. We only need ticker (idx 0) and
        current price (idx 2).
        """
        if not isinstance(raw, str):
            return
        if "|H0STCNT0|" not in raw:
            return
        parts = raw.split("|")
        if len(parts) < 4:
            return
        body = parts[3]
        cols = body.split("^")
        if len(cols) < 3:
            return
        ticker = cols[0]
        try:
            price = float(cols[2])
        except ValueError:
            return
        self.handle_tick(ticker, price)


# ----------------------------------------------------------------------
async def _async_main() -> None:
    setup_logging()
    if not bool(get_setting("websocket.enabled", False)):
        log.info("websocket.disabled_in_settings")
        return

    # Subscribe to whatever the KIS account currently holds
    from .kis_client import KISClient

    state = KISClient().get_account_state()
    tickers = list(state.get("positions") or {})
    if not tickers:
        log.info("websocket.no_positions_to_subscribe")
        return

    stops = RealtimeStops()
    stops.subscribe(tickers)
    with contextlib.suppress(asyncio.CancelledError):
        await stops.run()


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
