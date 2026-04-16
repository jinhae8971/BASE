"""Claude-powered per-stock analysis with prompt caching."""
from __future__ import annotations

import json
import re

from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import get_settings
from .logging_setup import get_logger
from .models import GainerStock, NewsItem, StockAnalysis
from .news import fetch_news_for_ticker

log = get_logger(__name__)


def _load_system_prompt() -> str:
    settings = get_settings()
    return (settings.prompts_dir / "analyzer_system.md").read_text(encoding="utf-8")


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


def _build_stock_context(gainer: GainerStock) -> tuple[dict, list[NewsItem]]:
    """Fetch news for a single stock and build context dict."""
    news = fetch_news_for_ticker(gainer.ticker, limit=5)

    context = {
        "ticker": gainer.ticker,
        "name": gainer.name,
        "market": gainer.market,
        "market_cap_rank": gainer.market_cap_rank,
        "market_cap_krw": round(gainer.market_cap, 0),
        "trading_value_krw": round(gainer.trading_value, 0),
        "close_krw": gainer.close,
        "volume": gainer.volume,
        "change_pct_2d": round(gainer.change_pct_nd, 2),
        "change_pct_1d": round(gainer.change_pct_1d, 2),
        "recent_news": [
            {"title": n.title, "source": n.source, "url": n.url} for n in news
        ],
    }
    return context, news


def analyze_gainers(
    gainers: list[GainerStock],
    prior_narrative: str = "",
) -> list[StockAnalysis]:
    """Run Claude analysis for the full list of gainers in a single request."""
    if not gainers:
        return []

    contexts: list[dict] = []
    news_by_ticker: dict[str, list[NewsItem]] = {}
    for g in gainers:
        ctx, news = _build_stock_context(g)
        contexts.append(ctx)
        news_by_ticker[g.ticker] = news

    system = _load_system_prompt()
    user_text = (
        "다음은 최근 2거래일 상승률 상위 종목들입니다. "
        "시스템 프롬프트의 JSON 스키마에 맞춰 분석 결과를 반환해주세요.\n\n"
        f"{json.dumps({'stocks': contexts}, ensure_ascii=False, indent=2)}"
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
    analyses: list[StockAnalysis] = []
    for item, gainer in zip(analyses_raw, gainers, strict=False):
        analyses.append(
            StockAnalysis(
                ticker=item.get("ticker") or gainer.ticker,
                name=item.get("name") or gainer.name,
                pump_thesis=item.get("pump_thesis", ""),
                drivers=list(item.get("drivers") or []),
                risks=list(item.get("risks") or []),
                sector_tags=list(item.get("sector_tags") or []),
                confidence=float(item.get("confidence") or 0.0),
                news_used=news_by_ticker.get(gainer.ticker, []),
            )
        )

    log.info("produced %d analyses", len(analyses))
    return analyses
