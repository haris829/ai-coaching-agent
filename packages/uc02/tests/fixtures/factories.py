"""Builders shared by the test suite.

Everything is constructed explicitly: tests never rely on process-wide
singletons or on a ``.env`` file being present.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from uc02.application.context_assembly_service import ContextAssemblyService
from uc02.composition import get_assembly_service, get_current_user_provider
from uc02.domain.models.session import SessionIdentity
from uc02.domain.ports.identity import CurrentUserProvider
from uc02.domain.ports.repository import SessionContextRepository
from uc02.infrastructure.config.settings import Settings, get_settings
from uc02.infrastructure.identity.development_user_provider import DevelopmentUserProvider
from uc02.infrastructure.providers.mocks import (
    CoursesScenario,
    HistoryScenario,
    LegalScenario,
    MockCoursesProvider,
    MockLegalFootprintsProvider,
    MockNaricProvider,
    MockQuestionHistoryProvider,
    NaricScenario,
)
from uc02.infrastructure.repositories.in_memory_context_repository import (
    InMemorySessionContextRepository,
)
from uc02.main import create_app

FIXED_NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_settings(**overrides: Any) -> Settings:
    """Settings built from explicit values only -- no ``.env`` file is read."""
    defaults: dict[str, Any] = {
        "environment": "test",
        "provider_timeout_ms": 2000,
        "context_assembly_budget_ms": 3000,
        "question_history_limit": 20,
        "context_ttl_hours": 12,
        "allow_dev_session_ids": False,
        "debug_context_endpoint": False,
        "allow_force_refresh": False,
        "user_id_log_salt": "test-salt",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


@dataclass
class Harness:
    """A fully wired service plus the mocks behind it, for assertions."""

    service: ContextAssemblyService
    naric: MockNaricProvider
    courses: MockCoursesProvider
    legal: MockLegalFootprintsProvider
    history: MockQuestionHistoryProvider
    repository: InMemorySessionContextRepository
    settings: Settings

    @property
    def provider_call_count(self) -> int:
        return (
            self.naric.call_count
            + self.courses.call_count
            + self.legal.call_count
            + self.history.call_count
        )

    def reset_call_counts(self) -> None:
        for mock in (self.naric, self.courses, self.legal, self.history):
            mock.reset_calls()


def make_harness(
    *,
    naric: NaricScenario = NaricScenario.LEVEL_5,
    courses: CoursesScenario = CoursesScenario.MULTIPLE_ENROLMENTS,
    legal: LegalScenario = LegalScenario.COMPLETE,
    history: HistoryScenario = HistoryScenario.EXACTLY_20,
    settings: Settings | None = None,
    repository: SessionContextRepository | None = None,
    clock: Any = None,
) -> Harness:
    settings = settings or make_settings()
    naric_mock = MockNaricProvider(naric)
    courses_mock = MockCoursesProvider(courses)
    legal_mock = MockLegalFootprintsProvider(legal)
    history_mock = MockQuestionHistoryProvider(history)
    repo = repository or InMemorySessionContextRepository(ttl_hours=settings.context_ttl_hours)
    service = ContextAssemblyService(
        naric=naric_mock,
        courses=courses_mock,
        legal=legal_mock,
        history=history_mock,
        repository=repo,
        settings=settings,
        clock=clock or (lambda: FIXED_NOW),
    )
    return Harness(
        service=service,
        naric=naric_mock,
        courses=courses_mock,
        legal=legal_mock,
        history=history_mock,
        repository=repo,  # type: ignore[arg-type]
        settings=settings,
    )


def make_client(
    harness: Harness,
    *,
    user_provider: CurrentUserProvider | None = None,
) -> TestClient:
    """A TestClient wired to ``harness``, with every collaborator overridden."""
    application = create_app(harness.settings)
    provider = user_provider or DevelopmentUserProvider(harness.settings.dev_user_id_header)
    application.dependency_overrides[get_settings] = lambda: harness.settings
    application.dependency_overrides[get_assembly_service] = lambda: harness.service
    application.dependency_overrides[get_current_user_provider] = lambda: provider
    return TestClient(application)


def auth(user_id: str, settings: Settings | None = None) -> dict[str, str]:
    """Headers carrying the caller's identity (never the request body)."""
    header = (settings or make_settings()).dev_user_id_header
    return {header: user_id}


def make_identity(
    session_id: str = "sess-fixture-1",
    user_id: str = "learner-1",
    origin: str = "caller",
) -> SessionIdentity:
    return SessionIdentity(
        session_id=session_id,
        user_id=user_id,
        requested_at=FIXED_NOW,
        session_id_origin=origin,
    )
