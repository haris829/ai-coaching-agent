"""Configuration.

No hard-coded URLs, keys or timeouts anywhere in business logic.  Everything
tunable is here, and every provider selection is a string that a registry
resolves.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- provider selection ----------------------------------------------
    # One string per port.  Changing one of these is the entire config half
    # of the integration swap.
    generator: str = Field(default="fake", alias="GENERATOR")
    learner_context_provider: str = Field(
        default="mock", alias="LEARNER_CONTEXT_PROVIDER"
    )
    intent_classifier: str = Field(default="mock", alias="INTENT_CLASSIFIER")
    dialogue_repository: str = Field(default="memory", alias="DIALOGUE_REPOSITORY")
    session_mode_repository: str = Field(
        default="memory", alias="SESSION_MODE_REPOSITORY"
    )
    interaction_log_repository: str = Field(
        default="memory", alias="INTERACTION_LOG_REPOSITORY"
    )
    current_user_provider: str = Field(default="header", alias="CURRENT_USER_PROVIDER")

    # -- behaviour --------------------------------------------------------
    socratic_exchange_cap: int = Field(
        default=5, ge=1, le=50, alias="SOCRATIC_EXCHANGE_CAP"
    )
    loop_similarity_threshold: float = Field(
        default=0.8, ge=0.0, le=1.0, alias="LOOP_SIMILARITY_THRESHOLD"
    )

    # -- latency ----------------------------------------------------------
    generation_timeout_ms: int = Field(
        default=10_000, ge=1, alias="GENERATION_TIMEOUT_MS"
    )
    generation_target_p95_ms: int = Field(
        default=3_000, ge=1, alias="GENERATION_TARGET_P95_MS"
    )

    # -- gates ------------------------------------------------------------
    allow_dev_session_ids: bool = Field(default=False, alias="ALLOW_DEV_SESSION_IDS")

    # -- real-provider settings (unused while GENERATOR=fake) -------------
    # Present so that a ConfiguredGenerator can be wired without a code change.
    # No defaults that could reach a network.
    generator_provider: str | None = Field(default=None, alias="GENERATOR_PROVIDER")
    generator_model: str | None = Field(default=None, alias="GENERATOR_MODEL")
    generator_api_key: str | None = Field(default=None, alias="GENERATOR_API_KEY")
    generator_base_url: str | None = Field(default=None, alias="GENERATOR_BASE_URL")

    learner_context_base_url: str | None = Field(
        default=None, alias="LEARNER_CONTEXT_BASE_URL"
    )
    learner_context_api_key: str | None = Field(
        default=None, alias="LEARNER_CONTEXT_API_KEY"
    )

    # -- logging ----------------------------------------------------------
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def generation_timeout_seconds(self) -> float:
        return self.generation_timeout_ms / 1000.0


def load_settings(**overrides: object) -> Settings:
    """Build settings, allowing tests to override without touching the env."""
    return Settings(**overrides)  # type: ignore[arg-type]
