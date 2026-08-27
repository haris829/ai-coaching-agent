"""Composition root and provider registry.

Provider selection is table-driven. There is no ``if provider == "mock"`` chain
anywhere in the codebase, and there is no silent fallback: an unknown provider
name raises :class:`ConfigurationError` at startup and the service refuses to
start.

Adding a real provider requires exactly three things:

1. one new adapter file under ``uc07/adapters/real/``;
2. one line in the matching registry below;
3. one environment-variable change.

Nothing else - not the domain models, the application service, the API, the
existing mock adapters, persistence, or the existing tests.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypeVar

from uc07.adapters.clock import SystemClock
from uc07.adapters.foreign import (
    EXTERNAL_LEARNER_ID,
    NEXUS_PAYLOAD,
    ForeignCoursesProvider,
    ForeignFeedbackProvider,
    ForeignInteractionLogProvider,
    ForeignLearnerProfileProvider,
)
from uc07.adapters.identity import HeaderCurrentUserProvider, StaticCurrentUserProvider
from uc07.adapters.mock import (
    MockCoursesProvider,
    MockFeedbackProvider,
    MockInteractionLogProvider,
    MockLearnerProfileProvider,
    get_scenario,
)
from uc07.application.config import Settings
from uc07.application.service import GapReportService
from uc07.application.topic_descriptions import TopicDescriptionRegistry
from uc07.domain.errors import ConfigurationError
from uc07.ports import (
    Clock,
    CoursesProvider,
    CurrentUserProvider,
    FeedbackProvider,
    GapReportRepository,
    InteractionLogProvider,
    LearnerProfileProvider,
)
from uc07.adapters.persistence import InMemoryGapReportRepository

T = TypeVar("T")
Factory = Callable[[Settings], T]

# ---------------------------------------------------------------------------
# Registries - one line per (port, provider) pair.
# ---------------------------------------------------------------------------

INTERACTION_LOG_PROVIDERS: dict[str, Factory[InteractionLogProvider]] = {
    "mock": lambda settings: MockInteractionLogProvider(
        get_scenario(settings.mock_scenario).interactions
    ),
    "foreign": lambda settings: ForeignInteractionLogProvider(NEXUS_PAYLOAD),
    # "acme": lambda settings: AcmeInteractionLogProvider(...),  <-- real adapter
}

FEEDBACK_PROVIDERS: dict[str, Factory[FeedbackProvider]] = {
    "mock": lambda settings: MockFeedbackProvider(
        get_scenario(settings.mock_scenario).feedback
    ),
    "foreign": lambda settings: ForeignFeedbackProvider(
        NEXUS_PAYLOAD, external_id=EXTERNAL_LEARNER_ID
    ),
}

PROFILE_PROVIDERS: dict[str, Factory[LearnerProfileProvider]] = {
    "mock": lambda settings: MockLearnerProfileProvider(
        get_scenario(settings.mock_scenario).profiles
    ),
    "foreign": lambda settings: ForeignLearnerProfileProvider(NEXUS_PAYLOAD),
}

COURSES_PROVIDERS: dict[str, Factory[CoursesProvider]] = {
    "mock": lambda settings: MockCoursesProvider(
        get_scenario(settings.mock_scenario).courses
    ),
    "foreign": lambda settings: ForeignCoursesProvider(NEXUS_PAYLOAD),
}

CURRENT_USER_PROVIDERS: dict[str, Factory[CurrentUserProvider]] = {
    "header": lambda settings: HeaderCurrentUserProvider(settings.current_user_header),
    "static": lambda settings: StaticCurrentUserProvider(EXTERNAL_LEARNER_ID),
}


def resolve(
    registry: Mapping[str, Factory[T]], name: str, *, env_var: str, settings: Settings
) -> T:
    """Look a provider up in its registry, failing loudly on an unknown name."""
    factory = registry.get(name)
    if factory is None:
        known = ", ".join(sorted(registry))
        raise ConfigurationError(
            f"{env_var}='{name}' is not a registered provider. "
            f"Registered providers: {known}. "
            "Register the adapter in uc07/composition.py; UC-07 never falls back "
            "to the mock adapter."
        )
    return factory(settings)


@dataclass(frozen=True, slots=True)
class Container:
    """Everything the API layer needs, wired once at startup."""

    settings: Settings
    service: GapReportService
    current_user: CurrentUserProvider
    repository: GapReportRepository
    clock: Clock


def build_container(
    settings: Settings | None = None,
    *,
    repository: GapReportRepository | None = None,
    clock: Clock | None = None,
) -> Container:
    """Wire the service graph. Raises ``ConfigurationError`` on bad configuration."""
    settings = settings or Settings()

    interactions = resolve(
        INTERACTION_LOG_PROVIDERS,
        settings.interaction_log_provider,
        env_var="INTERACTION_LOG_PROVIDER",
        settings=settings,
    )
    feedback = resolve(
        FEEDBACK_PROVIDERS,
        settings.feedback_provider,
        env_var="FEEDBACK_PROVIDER",
        settings=settings,
    )
    profiles = resolve(
        PROFILE_PROVIDERS,
        settings.profile_provider,
        env_var="PROFILE_PROVIDER",
        settings=settings,
    )
    courses = resolve(
        COURSES_PROVIDERS,
        settings.courses_provider,
        env_var="COURSES_PROVIDER",
        settings=settings,
    )
    current_user = resolve(
        CURRENT_USER_PROVIDERS,
        settings.current_user_provider,
        env_var="CURRENT_USER_PROVIDER",
        settings=settings,
    )

    descriptions = TopicDescriptionRegistry.from_path(
        settings.topic_description_registry_path
    )
    resolved_repository = repository or InMemoryGapReportRepository()
    resolved_clock = clock or SystemClock()

    service = GapReportService(
        interactions=interactions,
        feedback=feedback,
        profiles=profiles,
        courses=courses,
        repository=resolved_repository,
        clock=resolved_clock,
        descriptions=descriptions,
        thresholds=settings.thresholds(),
    )
    return Container(
        settings=settings,
        service=service,
        current_user=current_user,
        repository=resolved_repository,
        clock=resolved_clock,
    )
