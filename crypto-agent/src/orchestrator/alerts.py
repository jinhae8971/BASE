"""Lightweight Telegram alerter.

Used by the scheduler and DailyWorkflow to push run-start / run-done /
failure events to a single chat. Completely optional: if
`TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` are empty, every call is a
silent no-op. That keeps the dev loop and the test suite hermetic.
"""

from __future__ import annotations

import httpx

from src.config import get_settings
from src.logging import get_logger

log = get_logger("alerts")


class TelegramAlerter:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url="https://api.telegram.org", timeout=5.0
            )
        return self._client

    async def send(self, text: str, level: str = "info") -> bool:
        s = get_settings()
        if not s.telegram_bot_token or not s.telegram_chat_id:
            return False  # silently disabled
        prefix = {"info": "INFO", "warn": "WARN", "error": "ERROR"}.get(level, "-")
        body = f"{prefix} {text}"
        try:
            client = await self._get_client()
            resp = await client.post(
                f"/bot{s.telegram_bot_token}/sendMessage",
                json={"chat_id": s.telegram_chat_id, "text": body[:4000]},
            )
            if resp.status_code >= 400:
                log.warning("alerts.telegram_rejected", status=resp.status_code)
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("alerts.telegram_failed", err=str(exc))
            return False

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
