"""Central configuration loaded from environment.

All runtime knobs live here so that agents, data sources, and the executor
share the same source of truth. Values come from environment variables (see
`.env.example`) via pydantic-settings.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    DRY = "dry"       # no orders, no real LLM; good for wiring tests
    PAPER = "paper"   # Binance testnet, real LLM
    LIVE = "live"     # real money, real LLM -- P5 gate required


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Mode
    trading_mode: TradingMode = TradingMode.DRY

    # Capital
    initial_capital_usdt: float = 1000.0
    base_currency: str = "USDT"

    # Anthropic
    anthropic_api_key: str = ""
    agent_model_fast: str = "claude-sonnet-4-6"
    agent_model_strong: str = "claude-opus-4-6"
    anthropic_daily_budget_usd: float = 5.0

    # Binance
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True

    # Free-tier data
    cryptopanic_api_key: str = ""
    fred_api_key: str = ""

    # Infra
    postgres_dsn: str = "postgresql+psycopg://crypto:crypto@localhost:5432/crypto"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"

    # Risk guardrails (percent)
    max_position_pct: float = 25.0
    min_core_pct: float = 40.0
    stop_loss_pct: float = 8.0
    daily_loss_halt_pct: float = 3.0
    weekly_loss_reduce_pct: float = 7.0
    mdd_circuit_breaker_pct: float = 15.0

    # Live trading hard caps (independent of agent decisions).
    live_max_capital_usdt: float = 1000.0   # equity cap: never touch funds above this
    live_max_order_usdt: float = 250.0       # per-order notional ceiling

    # Observability
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    log_level: str = "INFO"

    # Universe
    core_assets: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    universe_size: int = 20
    min_24h_volume_usd: float = 50_000_000.0
    min_listing_age_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
