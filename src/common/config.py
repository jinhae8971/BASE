"""Centralized config loader (YAML + env vars)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.yaml"


class EnvSettings(BaseSettings):
    """Secrets and runtime env, loaded from .env."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    claude_reasoning_model: str = "claude-opus-4-6"
    claude_fast_model: str = "claude-haiku-4-5-20251001"

    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""
    kis_env: str = "paper"

    dart_api_key: str = ""
    ecos_api_key: str = ""

    # Upbit — normally entered in the dashboard and sealed under data_store/.
    # These env vars exist only as an escape hatch for headless/CI runs.
    upbit_access_key: str = ""
    upbit_secret_key: str = ""

    mais_data_dir: str = "./data_store"
    mais_log_level: str = "INFO"
    mais_tz: str = "Asia/Seoul"

    slack_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@lru_cache(maxsize=1)
def load_yaml_settings() -> dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return {}
    with SETTINGS_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_env() -> EnvSettings:
    return EnvSettings()


def get_setting(path: str, default: Any = None) -> Any:
    """Dot-path lookup into settings.yaml (e.g. 'risk.max_position_weight')."""
    data: Any = load_yaml_settings()
    for key in path.split("."):
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return data
