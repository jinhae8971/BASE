"""Strategy configuration: YAML defaults + dashboard overrides.

`config/settings.yaml::upbit` holds the checked-in defaults. Anything the user
changes in the 전략 tab is stored as a deep-merge patch under the
``config`` key of ``app_settings``, so the file on disk stays the baseline you
can always fall back to.
"""
from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from common.config import get_setting
from common.logging import get_logger

from .store import UpbitStore, get_store

log = get_logger(__name__)

CONFIG_KEY = "config"


class ScheduleConfig(BaseModel):
    enabled: bool = True
    selection_time: str = "09:10"
    monitor_interval_min: int = Field(default=5, ge=1, le=240)
    eod_exit_time: str = "08:50"
    snapshot_interval_min: int = Field(default=30, ge=5, le=1440)

    @field_validator("selection_time", "eod_exit_time")
    @classmethod
    def _valid_hhmm(cls, v: str) -> str:
        hh, _, mm = v.partition(":")
        if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError(f"시간 형식은 HH:MM 이어야 합니다: {v!r}")
        return f"{int(hh):02d}:{int(mm):02d}"


class UniverseConfig(BaseModel):
    min_trade_price_24h: float = Field(default=5_000_000_000, ge=0)
    max_trade_price_24h: float = Field(default=0, ge=0)
    min_price: float = Field(default=1.0, ge=0)
    exclude_warning: bool = True
    exclude_caution: bool = True
    # Which 주의 conditions actually disqualify a coin. TRADING_VOLUME_SOARING is
    # deliberately absent: a turnover spike is this strategy's entry signal, not
    # a reason to skip. "CAUTION" covers the older, untyped API response.
    caution_types: list[str] = Field(
        default_factory=lambda: [
            "CAUTION",
            "PRICE_FLUCTUATIONS",
            "DEPOSIT_AMOUNT_SOARING",
            "GLOBAL_PRICE_DIFFERENCES",
            "CONCENTRATION_OF_SMALL_ACCOUNTS",
        ]
    )
    exclude_stablecoins: bool = True
    max_candidates: int = Field(default=60, ge=5, le=200)
    stablecoins: list[str] = Field(
        default_factory=lambda: ["USDT", "USDC", "DAI", "TUSD", "BUSD", "PYUSD", "USDS"]
    )
    manual_blacklist: list[str] = Field(default_factory=list)


class ScoringWeights(BaseModel):
    volume: float = Field(default=0.30, ge=0, le=1)
    flow: float = Field(default=0.25, ge=0, le=1)
    technical: float = Field(default=0.30, ge=0, le=1)
    beta: float = Field(default=0.15, ge=0, le=1)

    def normalized(self) -> dict[str, float]:
        total = self.volume + self.flow + self.technical + self.beta
        if total <= 0:
            return {"volume": 0.30, "flow": 0.25, "technical": 0.30, "beta": 0.15}
        return {
            "volume": self.volume / total,
            "flow": self.flow / total,
            "technical": self.technical / total,
            "beta": self.beta / total,
        }


class ScoringConfig(BaseModel):
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    candle_count: int = Field(default=120, ge=30, le=200)
    intraday_unit: int = 60
    intraday_count: int = Field(default=72, ge=20, le=200)
    orderbook_depth: int = Field(default=15, ge=1, le=30)
    tick_count: int = Field(default=200, ge=20, le=500)


class EntryExitConfig(BaseModel):
    max_positions: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=62.0, ge=0, le=100)
    position_pct: float = Field(default=0.15, gt=0, le=1.0)
    max_krw_per_trade: float = Field(default=0, ge=0)
    min_krw_per_trade: float = Field(default=6000, ge=5000)
    take_profit_pct: float = Field(default=0.05, gt=0, le=2.0)
    stop_loss_pct: float = Field(default=0.03, gt=0, le=1.0)
    trailing_activate_pct: float = Field(default=0.03, ge=0, le=2.0)
    trailing_gap_pct: float = Field(default=0.02, ge=0, le=1.0)
    max_hold_hours: float = Field(default=22, gt=0, le=720)
    order_style: Literal["market", "limit"] = "market"
    limit_offset_bps: float = Field(default=10, ge=0, le=500)


class MaintenanceConfig(BaseModel):
    """Housekeeping for a system that is meant to run for months."""

    enabled: bool = True
    time: str = "04:30"                                   # daily, KST
    backup_keep: int = Field(default=7, ge=0, le=90)
    analyses_retention_days: int = Field(default=180, ge=0)
    events_retention_days: int = Field(default=90, ge=0)
    snapshots_retention_days: int = Field(default=730, ge=0)
    vacuum: bool = True

    @field_validator("time")
    @classmethod
    def _valid_hhmm(cls, v: str) -> str:
        hh, _, mm = v.partition(":")
        if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError(f"시간 형식은 HH:MM 이어야 합니다: {v!r}")
        return f"{int(hh):02d}:{int(mm):02d}"


class RiskConfig(BaseModel):
    daily_loss_kill_pct: float = Field(default=0.05, gt=0, le=1.0)
    max_total_exposure_pct: float = Field(default=0.80, gt=0, le=1.0)
    min_cash_buffer_pct: float = Field(default=0.10, ge=0, le=0.9)
    regime_exposure: dict[str, float] = Field(
        default_factory=lambda: {"risk_on": 1.0, "neutral": 0.6, "risk_off": 0.0}
    )


class UpbitConfig(BaseModel):
    """The whole tunable surface, exactly as the 전략 tab edits it."""

    mode: Literal["paper", "live"] = "paper"
    fee_rate: float = Field(default=0.0005, ge=0, le=0.01)
    paper_initial_krw: float = Field(default=10_000_000, gt=0)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    strategy: EntryExitConfig = Field(default_factory=EntryExitConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    maintenance: MaintenanceConfig = Field(default_factory=MaintenanceConfig)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def yaml_defaults() -> dict[str, Any]:
    """The checked-in baseline, minus keys that are not user-tunable."""
    raw = copy.deepcopy(get_setting("upbit", {}) or {})
    for key in ("base_url", "quote_currency", "storage", "dashboard"):
        raw.pop(key, None)
    return raw


def load_config(store: UpbitStore | None = None) -> UpbitConfig:
    """YAML defaults deep-merged with the dashboard's saved patch."""
    store = store or get_store()
    merged = _deep_merge(yaml_defaults(), store.get_override(CONFIG_KEY, {}) or {})
    try:
        return UpbitConfig.model_validate(merged)
    except Exception as exc:  # a bad patch must not brick the app
        log.error("upbit.config.invalid_override", error=str(exc))
        return UpbitConfig.model_validate(yaml_defaults())


def save_config(patch: dict[str, Any], store: UpbitStore | None = None) -> UpbitConfig:
    """Validate a partial update, persist it, and return the effective config."""
    store = store or get_store()
    current = store.get_override(CONFIG_KEY, {}) or {}
    candidate = _deep_merge(_deep_merge(yaml_defaults(), current), patch)
    config = UpbitConfig.model_validate(candidate)  # raises on invalid input

    new_patch = _deep_merge(current, patch)
    store.set_override(CONFIG_KEY, new_patch)
    store.log_event("info", "config", "전략 설정이 변경되었습니다.", {"patch": patch})
    log.info("upbit.config.saved", keys=sorted(patch))
    return config


def reset_config(store: UpbitStore | None = None) -> UpbitConfig:
    """Drop all overrides and fall back to `config/settings.yaml`."""
    store = store or get_store()
    store.delete_override(CONFIG_KEY)
    store.log_event("warning", "config", "전략 설정이 기본값으로 초기화되었습니다.")
    return load_config(store)


def set_mode(mode: str, store: UpbitStore | None = None) -> UpbitConfig:
    """Switch paper/live. Guarded at the API layer by an explicit confirmation."""
    if mode not in ("paper", "live"):
        raise ValueError("mode 는 'paper' 또는 'live' 여야 합니다.")
    store = store or get_store()
    store.log_event("warning", "config", f"거래 모드를 '{mode}' 로 전환했습니다.")
    return save_config({"mode": mode}, store)
