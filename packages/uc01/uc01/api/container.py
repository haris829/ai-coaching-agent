"""Composition root.

This is the **only** file that knows which concrete adapter implements which contract.
Swapping a mock for a real integration is one branch here plus one new module in
``uc01/adapters/real/`` — no change to ``uc01/application`` or ``uc01/domain``.
"""

from __future__ import annotations

import logging

from ..adapters.dev_identity import DevHeaderUserContextProvider
from ..adapters.mock import (
    MockCaseFileAdapter,
    MockCoursesAdapter,
    MockNaricAdapter,
    MockProfileAdapter,
)
from ..adapters.mock.scenarios import ScenarioSet
from ..application.session_service import SessionInitiationService
from ..config import Settings
from ..contracts.repository import SessionRepository
from ..contracts.services import (
    CaseFileService,
    CoursesService,
    NaricService,
    ProfileService,
    UserContextProvider,
)
from ..domain.greeting import LocalTemplateGreetingGenerator
from ..domain.prompts import SystemPromptRegistry
from ..persistence.db import Database
from ..persistence.memory_repository import InMemorySessionRepository
from ..persistence.sqlite_repository import SqliteSessionRepository

logger = logging.getLogger(__name__)

_REAL_ADAPTER_MISSING = (
    "{dependency} adapter is configured as {value!r}, but no real adapter is "
    "implemented yet. Add uc01/adapters/real/{module}.py implementing the "
    "{contract} contract and register it in uc01/api/container.py. "
    "See docs/ADAPTER_REPLACEMENT.md."
)


class AppContainer:
    """Owns process-wide singletons and builds per-request services."""

    def __init__(
        self, settings: Settings, repository: SessionRepository | None = None
    ) -> None:
        self.settings = settings
        self._database: Database | None = None
        self._repository = repository or self._build_repository(settings)
        self._identity = self._build_identity(settings)
        self._greeting = LocalTemplateGreetingGenerator()
        self._prompts = SystemPromptRegistry()

        if settings.uses_only_mock_adapters:
            logger.warning(
                "startup.mock_adapters_active",
                extra={"uc01": {"adapters": dict(settings.describe_adapters())}},
            )

    # -- singletons --------------------------------------------------------- #

    @property
    def repository(self) -> SessionRepository:
        return self._repository

    @property
    def identity(self) -> UserContextProvider:
        return self._identity

    @property
    def database(self) -> Database | None:
        return self._database

    def close(self) -> None:
        if self._database is not None:
            self._database.close()
            self._database = None

    # -- per-request service ------------------------------------------------ #

    def service(self, scenarios: ScenarioSet | None = None) -> SessionInitiationService:
        """Build the UC-01 service.

        A new instance per request is intentional and cheap: it lets the development
        scenario header select different mock behaviour without any global state.
        """
        active = scenarios or self.settings.scenarios
        return SessionInitiationService(
            naric_service=self._build_naric(active),
            courses_service=self._build_courses(active),
            case_service=self._build_cases(active),
            profile_service=self._build_profile(active),
            greeting_generator=self._greeting,
            repository=self._repository,
            prompts=self._prompts,
        )

    # -- adapter selection -------------------------------------------------- #

    def _build_naric(self, scenarios: ScenarioSet) -> NaricService:
        choice = self.settings.naric_adapter
        if choice == "mock":
            return MockNaricAdapter(scenarios.naric)
        # >>> Register the real NARIC adapter here. <<<
        raise NotImplementedError(
            _REAL_ADAPTER_MISSING.format(
                dependency="NARIC", value=choice, module="naric", contract="NaricService"
            )
        )

    def _build_courses(self, scenarios: ScenarioSet) -> CoursesService:
        choice = self.settings.courses_adapter
        if choice == "mock":
            return MockCoursesAdapter(scenarios.courses)
        # >>> Register the real Courses Agent adapter here. <<<
        raise NotImplementedError(
            _REAL_ADAPTER_MISSING.format(
                dependency="Courses",
                value=choice,
                module="courses",
                contract="CoursesService",
            )
        )

    def _build_cases(self, scenarios: ScenarioSet) -> CaseFileService:
        choice = self.settings.cases_adapter
        if choice == "mock":
            return MockCaseFileAdapter(scenarios.cases)
        # >>> Register the real Case Prep adapter here. <<<
        raise NotImplementedError(
            _REAL_ADAPTER_MISSING.format(
                dependency="Cases",
                value=choice,
                module="cases",
                contract="CaseFileService",
            )
        )

    def _build_profile(self, scenarios: ScenarioSet) -> ProfileService:
        choice = self.settings.profile_adapter
        if choice == "mock":
            return MockProfileAdapter(scenarios.profile)
        # >>> Register the real Profile adapter here. <<<
        raise NotImplementedError(
            _REAL_ADAPTER_MISSING.format(
                dependency="Profile",
                value=choice,
                module="profile",
                contract="ProfileService",
            )
        )

    # -- infrastructure selection ------------------------------------------- #

    def _build_repository(self, settings: Settings) -> SessionRepository:
        if settings.persistence == "memory":
            logger.warning("startup.in_memory_persistence")
            return InMemorySessionRepository()
        self._database = Database(settings.database_path)
        if settings.auto_migrate:
            applied = self._database.migrate()
            if applied:
                logger.info("startup.migrations_applied", extra={"uc01": {"applied": applied}})
        return SqliteSessionRepository(self._database)

    @staticmethod
    def _build_identity(settings: Settings) -> UserContextProvider:
        if settings.identity_provider == "dev":
            return DevHeaderUserContextProvider()
        # >>> Register the company authentication provider here. <<<
        raise NotImplementedError(
            _REAL_ADAPTER_MISSING.format(
                dependency="Identity",
                value=settings.identity_provider,
                module="identity",
                contract="UserContextProvider",
            )
        )


__all__ = ["AppContainer"]
