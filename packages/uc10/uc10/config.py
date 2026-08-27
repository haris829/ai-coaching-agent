"""Configuration.

Every number the flagging rule uses lives here and reaches business logic through
:class:`~uc10.ports.threshold_config_provider.ThresholdConfigProvider`.  This module is
the *only* place a default threshold literal is allowed to appear; the architecture test
that forbids hardcoded thresholds excludes it by name and no other file.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    #: Registry key selecting the InteractionProvider implementation. An unregistered key
    #: fails loudly at startup; there is never a silent fallback to a mock.
    interaction_provider: str = Field(default="mock", alias="INTERACTION_PROVIDER")

    #: SPECIFIED BY COMPANY: 30%, admin-configurable, never hardcoded in business logic.
    flag_down_rate_threshold: float = Field(
        default=0.30, ge=0.0, le=1.0, alias="FLAG_DOWN_RATE_THRESHOLD"
    )
    #: ASSUMED BY US (A-01): the specification leaves the minimum sample size open.
    #: Requires company confirmation. See docs/assumptions.md.
    flag_minimum_sample_size: int = Field(default=10, ge=1, alias="FLAG_MINIMUM_SAMPLE_SIZE")
    #: SPECIFIED BY COMPANY: rolling 7-day window.
    flag_window_days: int = Field(default=7, ge=1, alias="FLAG_WINDOW_DAYS")
    #: SPECIFIED BY COMPANY: historical ratings accepted within 24 hours of delivery.
    historical_rating_window_hours: int = Field(
        default=24, ge=1, alias="HISTORICAL_RATING_WINDOW_HOURS"
    )

    #: Dev-mode session minting, defaulted OFF. This component receives an opaque
    #: session_id and never creates one on a production path.
    allow_dev_session_minting: bool = Field(default=False, alias="ALLOW_DEV_SESSION_MINTING")

    #: Dev-only shared secret for the replaceable admin identity adapter (A-18).
    #: Unset means the admin endpoints deny every request rather than trusting a header.
    dev_admin_token: str | None = Field(default=None, alias="DEV_ADMIN_TOKEN")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Drop the cached settings so a changed environment takes effect.

    Used by the test that proves changing the configured threshold changes flagging
    behaviour with no code change, and by the composition root at startup.
    """
    get_settings.cache_clear()
