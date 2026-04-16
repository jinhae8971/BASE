"""Runtime configuration loaded from environment variables."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Secrets ---
    anthropic_api_key: str = Field(default="")
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")
    cryptopanic_key: str = Field(default="")
    coingecko_api_key: str = Field(default="")

    # --- Public ---
    dashboard_url: str = Field(default="https://example.github.io/crypto-research-agent/")

    # --- Tuning ---
    claude_model: str = Field(default="claude-sonnet-4-6")
    top_n_coins: int = Field(default=500)
    top_k_gainers: int = Field(default=5)
    min_volume_usd: float = Field(default=1_000_000)
    narrative_lookback_days: int = Field(default=7)

    # --- Paths (relative to repo root) ---
    repo_root: Path = Field(default=Path(__file__).resolve().parent.parent)

    @property
    def snapshots_dir(self) -> Path:
        return self.repo_root / "data" / "snapshots"

    @property
    def reports_dir(self) -> Path:
        return self.repo_root / "docs" / "reports"

    @property
    def prompts_dir(self) -> Path:
        return self.repo_root / "prompts"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
