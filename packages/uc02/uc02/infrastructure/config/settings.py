"""Configuration. Everything external is config-driven: no hard-coded URLs,
keys, timeouts or provider choices anywhere else in the codebase.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderChoice = Literal["mock", "company"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    # -- environment ------------------------------------------------------
    environment: Literal["development", "test", "production"] = "development"

    # -- provider selection (see uc02/infrastructure/providers/factory.py) --
    naric_provider: ProviderChoice = "mock"
    courses_provider: ProviderChoice = "mock"
    legal_provider: ProviderChoice = "mock"
    history_provider: ProviderChoice = "mock"

    # -- timing -----------------------------------------------------------
    provider_timeout_ms: int = Field(default=2000, ge=1)
    context_assembly_budget_ms: int = Field(default=3000, ge=1)

    # -- assembly rules ---------------------------------------------------
    question_history_limit: int = Field(default=20, ge=1, le=500)
    context_ttl_hours: int = Field(default=12, ge=1)

    # -- guarded switches (all default to the safe value) ------------------
    allow_dev_session_ids: bool = False
    debug_context_endpoint: bool = False
    #: Gates the internal/admin force-refresh path. Never honoured on the
    #: public initialize endpoint regardless of this value.
    allow_force_refresh: bool = False

    # -- identity ---------------------------------------------------------
    #: Header the replaceable development identity provider reads.
    dev_user_id_header: str = "X-User-Id"
    #: Header an internal caller must present for the admin refresh path.
    internal_admin_header: str = "X-Internal-Admin"

    # -- logging ----------------------------------------------------------
    log_level: str = "INFO"
    #: Salt for the one-way user id reference written to logs.
    user_id_log_salt: str = "uc02-local-dev-salt"

    @property
    def provider_timeout_seconds(self) -> float:
        return self.provider_timeout_ms / 1000.0

    @property
    def assembly_budget_seconds(self) -> float:
        return self.context_assembly_budget_ms / 1000.0

    def production_guard_violations(self) -> list[str]:
        """Switches that must not be on in production. Asserted by tests."""
        violations: list[str] = []
        if self.environment == "production":
            if self.allow_dev_session_ids:
                violations.append("ALLOW_DEV_SESSION_IDS must be false in production")
            if self.debug_context_endpoint:
                violations.append("DEBUG_CONTEXT_ENDPOINT must be false in production")
        return violations


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Tests override the FastAPI dependency instead."""
    return Settings()
