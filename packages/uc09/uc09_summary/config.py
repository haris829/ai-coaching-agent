"""Configuration. One environment variable selects each provider.

Every provider setting is the *name* of a registered implementation, resolved
through :mod:`uc09_summary.registry`. Swapping a mock for a real adapter is a
change to one of these values and nothing else.

Naming a provider that has no registered implementation is a startup failure,
by design. A service that quietly runs on fake data in production is worse than
one that refuses to start, so there is no fallback path here at all.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Environment variable prefix for every setting below.
ENV_PREFIX = "UC09_"


class Settings(BaseSettings):
    """Application settings, read from the environment or a ``.env`` file."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- provider selection -------------------------------------------------
    session_provider: str = "mock"
    interaction_provider: str = "mock"
    citation_provider: str = "mock"
    gap_report_provider: str = "mock"
    summary_generator: str = "fake"
    document_renderer: str = "simple"
    summary_repository: str = "memory"
    download_log_repository: str = "memory"
    clock: str = "system"
    current_user_provider: str = "header"

    # -- behaviour ----------------------------------------------------------
    provider_timeout_seconds: float = 5.0
    """Deadline an adapter must honour. Exceeding it is a ``ProviderTimeout``."""

    allow_dev_session_minting: bool = False
    """Dev-mode session id minting. Defaulted off; never enabled on a production path.

    This component receives an opaque ``session_id`` and does not create one.
    The flag exists so that a local developer can exercise the API without a
    session service, and it is refused loudly whenever it is off.
    """

    # -- upstream configuration used only by real adapters ------------------
    upstream_base_url: str = ""
    """Base URL for real adapters. Empty under the shipped mock configuration."""

    upstream_api_key: str = ""
    """Credential for real adapters. The full test suite runs with this unset."""

    summary_generator_path: str = "/v1/summaries:generate"
    """Path appended to the base URL by the ``http`` generator.

    Configurable because no endpoint is assumed. The ``http`` generator refuses
    to start unless ``upstream_base_url`` is set, so this default is never used
    to reach an address nobody configured.
    """

    # -- logging ------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True


def load_settings(**overrides: object) -> Settings:
    """Build settings, with explicit overrides for tests and the composition root."""
    return Settings(**overrides)  # type: ignore[arg-type]
