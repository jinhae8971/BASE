"""DefiLlama client — free, no key, generous limits.

Endpoints:
  - /v2/chains             : TVL by chain
  - /protocol/{slug}       : per-protocol TVL timeseries
  - /overview/dexs         : DEX volume overview
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from src.data.archive import append as archive_append
from src.data.cache import cached, default_cache
from src.data.http import build_client, configure_rate_limit, get_json

BASE_URL = "https://api.llama.fi"
HOST = "api.llama.fi"

# DefiLlama is permissive; keep ourselves honest at 120 req/min.
configure_rate_limit(HOST, requests=120, per_seconds=60.0)


@dataclass
class ChainTvl:
    name: str
    tvl_usd: float
    change_1d_pct: float
    change_7d_pct: float


class DefiLlamaClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or build_client(base_url=BASE_URL)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> DefiLlamaClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def chains(self) -> list[ChainTvl]:
        async def _load() -> list[dict[str, Any]]:
            raw = await get_json(self._client, "/v2/chains", host=HOST)
            archive_append("defillama_chains", {"count": len(raw)})
            return raw

        rows = await cached("llama:chains", ttl=600, loader=_load, cache=default_cache())
        return [
            ChainTvl(
                name=r.get("name", ""),
                tvl_usd=float(r.get("tvl") or 0.0),
                change_1d_pct=float(r.get("change_1d") or 0.0),
                change_7d_pct=float(r.get("change_7d") or 0.0),
            )
            for r in rows
        ]

    async def protocol(self, slug: str) -> dict[str, Any]:
        async def _load() -> dict[str, Any]:
            return await get_json(self._client, f"/protocol/{slug}", host=HOST)

        return await cached(f"llama:proto:{slug}", ttl=600, loader=_load, cache=default_cache())
