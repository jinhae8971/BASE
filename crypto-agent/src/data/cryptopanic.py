"""CryptoPanic free-tier news client with a daily budget manager.

Free tier limit: 500 requests per day. We enforce the budget locally using a
per-UTC-day counter persisted in memory -- once the budget is exhausted
further calls raise `BudgetExceeded`. Phase 2 will persist the counter to
Postgres so it survives restarts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from src.config import get_settings
from src.data.archive import append as archive_append
from src.data.http import build_client, configure_rate_limit, get_json
from src.logging import get_logger

log = get_logger("cryptopanic")

BASE_URL = "https://cryptopanic.com"
HOST = "cryptopanic.com"
DAILY_BUDGET = 500

# Be conservative on per-minute pacing.
configure_rate_limit(HOST, requests=30, per_seconds=60.0)


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class NewsItem:
    title: str
    url: str
    published_at: datetime
    currencies: list[str]
    votes: dict[str, int]
    kind: str


@dataclass
class _Budget:
    day: str = ""
    used: int = 0
    reserved: list[int] = field(default_factory=list)

    def check_and_increment(self, now: datetime) -> None:
        today = now.strftime("%Y-%m-%d")
        if today != self.day:
            self.day = today
            self.used = 0
        if self.used >= DAILY_BUDGET:
            raise BudgetExceeded(
                f"CryptoPanic daily budget {DAILY_BUDGET} exhausted for {today}"
            )
        self.used += 1


_budget = _Budget()


def budget_used() -> int:
    return _budget.used


class CryptoPanicClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or build_client(base_url=BASE_URL)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> CryptoPanicClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def posts(
        self,
        currencies: list[str] | None = None,
        kind: str = "news",
        filter_: str | None = None,
    ) -> list[NewsItem]:
        s = get_settings()
        if not s.cryptopanic_api_key:
            log.warning("cryptopanic.no_key")
            return []

        _budget.check_and_increment(datetime.now(UTC))

        params: dict[str, Any] = {
            "auth_token": s.cryptopanic_api_key,
            "kind": kind,
            "public": "true",
        }
        if currencies:
            params["currencies"] = ",".join(currencies)
        if filter_:
            params["filter"] = filter_

        raw = await get_json(self._client, "/api/free/v1/posts/", params=params, host=HOST)
        results = raw.get("results", []) if isinstance(raw, dict) else []
        archive_append("cryptopanic_posts", {"count": len(results), "budget_used": _budget.used})
        return [_parse(item) for item in results]


def _parse(item: dict[str, Any]) -> NewsItem:
    published = item.get("published_at") or item.get("created_at") or ""
    try:
        ts = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        ts = datetime.now(UTC)
    currencies = [c.get("code", "") for c in item.get("currencies") or []]
    return NewsItem(
        title=item.get("title", ""),
        url=item.get("url", ""),
        published_at=ts,
        currencies=currencies,
        votes=item.get("votes") or {},
        kind=item.get("kind", "news"),
    )
