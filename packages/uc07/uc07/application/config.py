"""Configuration. No threshold, URL or secret is hard-coded in business logic.

Business logic receives :class:`AnalysisThresholds` (a plain value object) rather
than reading the environment, so the rules stay testable and the only place that
knows about the environment is the composition root.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True, slots=True)
class AnalysisThresholds:
    """Every tunable rule the analysis needs, in one immutable value object."""

    gap_report_threshold: int
    min_topic_areas: int
    explain_differently_struggle_threshold: int
    low_rating_struggle_threshold: int
    follow_up_struggle_threshold: int


class Settings(BaseSettings):
    """Environment-driven settings.

    Provider names are resolved through the provider registry in
    :mod:`uc07.composition`. An unknown name fails loudly at startup; there is no
    silent fallback to the mock adapters.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- provider selection ------------------------------------------------
    interaction_log_provider: str = "mock"
    feedback_provider: str = "mock"
    profile_provider: str = "mock"
    courses_provider: str = "mock"

    # --- analysis thresholds ----------------------------------------------
    gap_report_threshold: int = Field(default=10, ge=1)
    min_topic_areas: int = Field(default=3, ge=1)
    explain_differently_struggle_threshold: int = Field(default=2, ge=1)
    low_rating_struggle_threshold: int = Field(default=1, ge=1)
    follow_up_struggle_threshold: int = Field(default=2, ge=1)

    # --- topic descriptions (registry only, never generated) --------------
    topic_description_registry_path: Path = Path("uc07/config/topic_descriptions.json")

    # --- mock adapters ----------------------------------------------------
    mock_scenario: str = "struggle_mixed"

    # --- identity seam (not production auth) ------------------------------
    current_user_provider: str = "header"
    current_user_header: str = "X-User-Id"

    # --- upstream call budget (used by real adapters) ---------------------
    provider_timeout_seconds: float = Field(default=5.0, gt=0)

    def thresholds(self) -> AnalysisThresholds:
        return AnalysisThresholds(
            gap_report_threshold=self.gap_report_threshold,
            min_topic_areas=self.min_topic_areas,
            explain_differently_struggle_threshold=(
                self.explain_differently_struggle_threshold
            ),
            low_rating_struggle_threshold=self.low_rating_struggle_threshold,
            follow_up_struggle_threshold=self.follow_up_struggle_threshold,
        )
