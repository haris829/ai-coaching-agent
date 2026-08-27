"""Configuration. Environment variables only; no secrets, no URLs invented here.

The names in the scope document are used verbatim and unprefixed.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Weekday(str, Enum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"

    @property
    def isoweekday(self) -> int:
        return list(Weekday).index(self) + 1


class PersistenceBackend(str, Enum):
    MEMORY = "memory"
    JSONFILE = "jsonfile"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- provider selection (resolved through the registry) -----------------
    activity_provider: str = Field(default="mock", alias="ACTIVITY_PROVIDER")
    gap_report_provider: str = Field(default="mock", alias="GAP_REPORT_PROVIDER")

    # -- streak rules -------------------------------------------------------
    streak_window_hours: int = Field(default=24, ge=1, le=168, alias="STREAK_WINDOW_HOURS")
    badge_milestones: tuple[int, ...] = Field(default=(10, 50, 100), alias="BADGE_MILESTONES")
    freeze_min_streak_days: int = Field(default=7, ge=1, alias="FREEZE_MIN_STREAK_DAYS")
    freeze_per_calendar_month: int = Field(default=1, ge=1, alias="FREEZE_PER_CALENDAR_MONTH")
    #: A-12. Not specified by the company; an unanswered offer must expire.
    freeze_offer_expiry_hours: int = Field(default=24, ge=1, alias="FREEZE_OFFER_EXPIRY_HOURS")

    # -- weekly summary -----------------------------------------------------
    weekly_summary_day: Weekday = Field(default=Weekday.MONDAY, alias="WEEKLY_SUMMARY_DAY")

    # -- infrastructure -----------------------------------------------------
    persistence: PersistenceBackend = Field(default=PersistenceBackend.MEMORY, alias="PERSISTENCE")
    persistence_dir: str = Field(default=".uc08-data", alias="PERSISTENCE_DIR")
    provider_timeout_seconds: float = Field(default=5.0, gt=0, alias="PROVIDER_TIMEOUT_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # -- session identity ---------------------------------------------------
    #: UC-08 receives an opaque session_id and never creates one on a
    #: production path. Dev minting is gated by this flag and defaults off.
    allow_dev_session_minting: bool = Field(default=False, alias="ALLOW_DEV_SESSION_MINTING")
    #: Minimal replaceable identity: the header the dev identity adapter reads.
    dev_identity_header: str = Field(default="X-UC08-Subject", alias="DEV_IDENTITY_HEADER")

    @field_validator("badge_milestones", mode="before")
    @classmethod
    def _parse_milestones(cls, value: Any) -> Any:
        if isinstance(value, str):
            parts = [chunk.strip() for chunk in value.split(",") if chunk.strip()]
            if not parts:
                raise ValueError("BADGE_MILESTONES must list at least one threshold")
            return tuple(int(part) for part in parts)
        return value

    @field_validator("badge_milestones")
    @classmethod
    def _sorted_positive_milestones(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(threshold <= 0 for threshold in value):
            raise ValueError("BADGE_MILESTONES thresholds must be positive")
        if len(set(value)) != len(value):
            raise ValueError("BADGE_MILESTONES must not repeat a threshold")
        return tuple(sorted(value))

    @field_validator("activity_provider", "gap_report_provider")
    @classmethod
    def _non_empty_provider(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("provider name must not be empty")
        return value.strip()

    @model_validator(mode="after")
    def _freeze_allowance_is_representable(self) -> Settings:
        # The streak record shape is fixed by the platform and carries a single
        # freeze_used_at. More than one freeze per calendar month cannot be
        # represented without a usage counter that the fixed shape does not
        # have. Fail loudly rather than silently mis-count (A-11).
        if self.freeze_per_calendar_month != 1:
            raise ValueError(
                "FREEZE_PER_CALENDAR_MONTH must be 1: the platform streak record carries a single "
                "freeze_used_at, so a larger allowance is not representable. Changing this is a "
                "contract conversation, not a config change."
            )
        return self


def load_settings(**overrides: Any) -> Settings:
    """Build settings from the environment, with explicit test overrides."""
    return Settings(**overrides)
