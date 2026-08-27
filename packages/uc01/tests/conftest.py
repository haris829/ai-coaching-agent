"""Shared test fixtures.

Two ways of testing are used deliberately:

* **API tests** go through the real HTTP surface with ``TestClient``, so status codes,
  schemas and response-safety are covered as a user would meet them.
* **Service tests** build ``SessionInitiationService`` directly with stub adapters from
  ``tests/stubs.py``, which proves UC-01 depends on the contracts and not on the mocks.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from uc01.adapters.mock.scenarios import ScenarioSet
from uc01.api.app import create_app
from uc01.application.session_service import SessionInitiationService
from uc01.config import Settings
from uc01.domain.greeting import LocalTemplateGreetingGenerator
from uc01.persistence.db import Database
from uc01.persistence.memory_repository import InMemorySessionRepository
from uc01.persistence.sqlite_repository import SqliteSessionRepository

from .stubs import (
    StubCaseFileService,
    StubCoursesService,
    StubNaricService,
    StubProfileService,
)

ALICE = "dev-alice"
BOB = "dev-bob"
CAROL = "dev-carol"


def auth(token: str = ALICE) -> dict[str, str]:
    """Authorization header for a development user."""
    return {"Authorization": f"Bearer {token}"}


def scenarios(**overrides: str) -> dict[str, str]:
    """Build the development scenario header, e.g. ``scenarios(courses="unavailable")``."""
    return {
        "X-Dev-Scenarios": ",".join(f"{key}={value}" for key, value in overrides.items())
    }


# --------------------------------------------------------------------------- #
# API-level fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Isolated SQLite file per test, dev mode on, frontend off."""
    return Settings(
        environment="test",
        dev_mode=True,
        persistence="sqlite",
        database_path=str(tmp_path / "uc01-test.sqlite3"),
        auto_migrate=True,
        scenarios=ScenarioSet(),
        dev_scenario_header_enabled=True,
        log_level="WARNING",
        log_format="text",
        expose_error_details=False,
        serve_frontend=False,
    )


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings, configure_logs=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def repository(client) -> Any:
    """The repository the running app is actually writing to."""
    return client.app.state.container.repository


# --------------------------------------------------------------------------- #
# Service-level fixtures
# --------------------------------------------------------------------------- #


class FrozenClock:
    """Deterministic clock; each call advances by one second."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        current = self._now
        self._now = self._now + timedelta(seconds=1)
        return current


class SequentialIds:
    def __init__(self) -> None:
        self._counter = 0

    def new_session_id(self) -> str:
        self._counter += 1
        return f"sess_test_{self._counter:04d}"


@pytest.fixture
def memory_repository() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture
def sqlite_repository(tmp_path: Path):
    database = Database(str(tmp_path / "repo-test.sqlite3"))
    database.migrate()
    try:
        yield SqliteSessionRepository(database)
    finally:
        database.close()


@pytest.fixture
def build_service(memory_repository):
    """Factory building the UC-01 service from stub adapters.

    Any stub can be overridden per test, e.g.
    ``build_service(naric=StubNaricService(fail=True))``.
    """

    def _build(
        *,
        naric: Any | None = None,
        courses: Any | None = None,
        cases: Any | None = None,
        profile: Any | None = None,
        greeting: Any | None = None,
        repository: Any | None = None,
    ) -> SessionInitiationService:
        return SessionInitiationService(
            naric_service=naric or StubNaricService(),
            courses_service=courses or StubCoursesService(),
            case_service=cases or StubCaseFileService(),
            profile_service=profile or StubProfileService(),
            greeting_generator=greeting or LocalTemplateGreetingGenerator(),
            repository=repository or memory_repository,
            clock=FrozenClock(),
            id_generator=SequentialIds(),
        )

    return _build


def response_text(payload: Mapping[str, Any]) -> str:
    """Flatten a JSON payload to a string for leak assertions."""
    import json

    return json.dumps(payload)
