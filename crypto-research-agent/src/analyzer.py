"""Claude-powered per-coin analysis with prompt caching."""
from __future__ import annotations

import json
import re

from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import get_settings
from .fetcher import fetch_coin_detail
from .logging_setup import get_logger
from .models import CoinAnalysis, GainerCoin, NewsItem
from .news import fetch_news_for_symbol

log = get_logger(__name__)

MAX_DESCRIPTION_CHARS = 1500


def _load_system_prompt() -> str:
    settings = get_settings()
    path = settings.prompts_dir / "analyzer_system.md"
    return path.read_text(encoding="utf-8")


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _build_coin_context(gainer: GainerCoin) -> tuple[dict, list[NewsItem]]:
    """Fetch description + news for a single coin and return a context dict."""
    try:
        detail = fetch_coin_detail(gainer.id)
    except Exception as exc:  # noqa: BLE001
        log.warning("coin detail fetch failed for %s: %s", gainer.id, exc)
        detail = {}

    description = _strip_html(
        (detail.get("description") or {}).get("en", "")
    )[:MAX_DESCRIPTION_CHARS]
    categories = [c for c in (detail.get("categories") or []) if c]

    news = fetch_news_for_symbol(gainer.symbol, limit=5)

    context = {
        "coin_id": gainer.id,
        "symbol": gainer.symbol.upper(),
        "name": gainer.name,
        "market_cap_rank": gainer.market_cap_rank,
        "market_cap_usd": round(gainer.market_cap, 0),
        "volume_24h_usd": round(gainer.total_volume, 0),
        "current_price_usd": gainer.current_price,
        "price_change_48h_pct": round(gainer.change_48h_pct, 2),
        "price_change_24h_pct": (
            round(gainer.change_24h_pct, 2) if gainer.change_24h_pct is not None else None
        ),
        "coingecko_categories": categories,
        "description_snippet": description,
        "recent_news": [
            {"title": n.title, "source": n.source, "url": n.url} for n in news
        ],
    }
    return context, news


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _call_claude(system: str, user_text: str) -> str:
    settings = get_settings()
    client = Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_text}],
    )
    # Concatenate text blocks
    parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    log.info(
        "claude usage: input=%s cache_read=%s cache_write=%s output=%s",
        response.usage.input_tokens,
        getattr(response.usage, "cache_read_input_tokens", 0),
        getattr(response.usage, "cache_creation_input_tokens", 0),
        response.usage.output_tokens,
    )
    return "".join(parts)


def _extract_json(text: str) -> dict:
    """Tolerantly extract the first JSON object from a model response."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    # Try parsing the whole thing first
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    # Find the first { and try json.loads from there
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in model response")
    for end in range(len(text), start, -1):
        if text[end - 1] != "}":
            continue
        try:
            result = json.loads(text[start:end])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            continue
    raise ValueError("no valid JSON object found in model response")


def analyze_gainers(
    gainers: list[GainerCoin],
    prior_narrative: str = "",
) -> list[CoinAnalysis]:
    """Run Claude analysis for the full list of gainers in a single request.

    Batching all coins into one call lets the cached system prompt amortize
    across them and produces a consistent perspective.
    """
    if not gainers:
        return []

    contexts: list[dict] = []
    news_by_id: dict[str, list[NewsItem]] = {}
    for g in gainers:
        ctx, news = _build_coin_context(g)
        contexts.append(ctx)
        news_by_id[g.id] = news

    system = _load_system_prompt()
    user_text = (
        "Analyze the following 48-hour top gainers. Return JSON per the schema "
        "in the system prompt.\n\n"
        f"{json.dumps({'coins': contexts}, ensure_ascii=False, indent=2)}"
    )
    if prior_narrative:
        user_text += f"\n\nYesterday's market narrative for context:\n{prior_narrative}"

    raw = _call_claude(system, user_text)
    try:
        data = _extract_json(raw)
    except Exception as exc:
        log.error("failed to parse analyzer JSON: %s\nraw=%s", exc, raw[:500])
        raise

    analyses_raw = data.get("analyses") or []
    if len(analyses_raw) != len(gainers):
        log.warning(
            "Claude returned %d analyses for %d gainers", len(analyses_raw), len(gainers),
        )
    analyses: list[CoinAnalysis] = []
    for item, gainer in zip(analyses_raw, gainers, strict=False):
        analyses.append(
            CoinAnalysis(
                coin_id=item.get("coin_id") or gainer.id,
                symbol=(item.get("symbol") or gainer.symbol).upper(),
                pump_thesis=item.get("pump_thesis", ""),
                drivers=list(item.get("drivers") or []),
                risks=list(item.get("risks") or []),
                category_tags=list(item.get("category_tags") or []),
                confidence=float(item.get("confidence") or 0.0),
                news_used=news_by_id.get(gainer.id, []),
            )
        )

    log.info("produced %d analyses", len(analyses))
    return analyses
