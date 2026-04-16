"""CoinGecko market data client."""
from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .config import get_settings
from .logging_setup import get_logger
from .models import CoinMarket

log = get_logger(__name__)

COINGECKO_PUBLIC = "https://api.coingecko.com/api/v3"
COINGECKO_DEMO = "https://pro-api.coingecko.com/api/v3"


def _base_url() -> str:
    settings = get_settings()
    if settings.coingecko_api_key:
        return COINGECKO_DEMO
    return COINGECKO_PUBLIC


def _headers() -> dict[str, str]:
    settings = get_settings()
    headers = {"accept": "application/json", "user-agent": "crypto-research-agent/0.1"}
    if settings.coingecko_api_key:
        headers["x-cg-demo-api-key"] = settings.coingecko_api_key
    return headers


def _is_retryable(exc: BaseException) -> bool:
    """Don't retry 403 (IP ban) or 401 (bad key) — they won't resolve."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code not in (401, 403)
    return True


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(min=2, max=16),
    retry=retry_if_exception(_is_retryable),
)
def _get(client: httpx.Client, path: str, params: dict) -> list | dict:
    response = client.get(path, params=params, timeout=30.0)
    response.raise_for_status()
    return response.json()


def fetch_top_markets(n: int = 500) -> list[CoinMarket]:
    """Fetch top-N coins by market cap (paginated, 250 per page)."""
    per_page = 250
    pages = (n + per_page - 1) // per_page
    collected: list[CoinMarket] = []

    with httpx.Client(base_url=_base_url(), headers=_headers()) as client:
        for page in range(1, pages + 1):
            data = _get(
                client,
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": per_page,
                    "page": page,
                    "price_change_percentage": "24h",
                    "sparkline": "false",
                },
            )
            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected CoinGecko response on page {page}")
            for row in data:
                try:
                    collected.append(CoinMarket(**row))
                except Exception as exc:  # noqa: BLE001
                    log.warning("skipping malformed coin row: %s", exc)

    log.info("fetched %d coins from CoinGecko", len(collected))
    return collected[:n]


def fetch_coin_detail(coin_id: str) -> dict:
    """Fetch descriptive metadata for a single coin (used by analyzer)."""
    with httpx.Client(base_url=_base_url(), headers=_headers()) as client:
        data = _get(
            client,
            f"/coins/{coin_id}",
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "true",
                "developer_data": "false",
                "sparkline": "false",
            },
        )
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected detail response for {coin_id}")
    return data
