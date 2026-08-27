"""Shared fixtures.

The suite runs with no network and no API key: every collaborator is an in-process
adapter behind a port.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.canaries import contains_canary
from uc10.adapters.memory.repositories import (
    InMemoryFlagRepository,
    InMemoryFlagWorkQueue,
    InMemoryRatingRepository,
)
from uc10.adapters.memory.support import (
    ManualClock,
    RecordingAdminNotificationSink,
    StaticThresholdConfigProvider,
)
from uc10.adapters.mock.identity import ConfiguredAdminIdentityProvider, HeaderCurrentUserProvider
from uc10.adapters.mock.interaction_provider import (
    LEARNER,
    OTHER_LEARNER,
    MockInteractionProvider,
)
from uc10.api.app import create_app
from uc10.api.deps import build_container
from uc10.config import Settings, reset_settings_cache
from uc10.logging_setup import configure_logging

FIXED_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
ADMIN_TOKEN = "dev-admin-token-for-tests"

LEARNER_HEADERS = {"X-User-Id": LEARNER}
OTHER_LEARNER_HEADERS = {"X-User-Id": OTHER_LEARNER}
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN, "X-Admin-Id": "admin_test"}


@pytest.fixture(scope="session", autouse=True)
def _route_all_logging_through_stdlib() -> None:
    """Render every log line through stdlib logging so ``caplog`` sees the whole suite."""
    configure_logging("INFO")


@pytest.fixture(autouse=True)
def no_learner_content_in_logs(caplog):
    """PRIVACY, asserted on EVERY test in the suite.

    Any log line emitted while a test runs is scanned for question text, response text or
    comment text. One canary fragment anywhere fails that test.
    """
    caplog.set_level(logging.DEBUG)
    yield
    captured = "\n".join(
        [record.getMessage() for record in caplog.records]
        + [str(record.args) for record in caplog.records if record.args]
    )
    leaked = contains_canary(captured)
    assert not leaked, f"learner content reached the logs: {leaked}"


@pytest.fixture(autouse=True)
def _isolated_settings_cache():
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(FIXED_NOW)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, DEV_ADMIN_TOKEN=ADMIN_TOKEN)


@pytest.fixture
def policy() -> StaticThresholdConfigProvider:
    """Explicit policy for tests: 30% and a minimum sample of 10 (the shipped defaults)."""
    return StaticThresholdConfigProvider(down_rate_threshold=0.30, minimum_sample_size=10)


@pytest.fixture
def ratings_repository() -> InMemoryRatingRepository:
    return InMemoryRatingRepository()


@pytest.fixture
def flag_repository() -> InMemoryFlagRepository:
    return InMemoryFlagRepository()


@pytest.fixture
def notifications() -> RecordingAdminNotificationSink:
    return RecordingAdminNotificationSink()


@pytest.fixture
def interactions(clock: ManualClock) -> MockInteractionProvider:
    return MockInteractionProvider(clock)


@pytest.fixture
def make_container(
    settings, clock, interactions, ratings_repository, flag_repository, notifications, policy
):
    """Build a container, overriding any collaborator a test needs to control."""

    def _make(**overrides):
        defaults = {
            "settings": settings,
            "clock": clock,
            "interactions": interactions,
            "ratings_repository": ratings_repository,
            "flag_repository": flag_repository,
            "flag_work_queue": InMemoryFlagWorkQueue(now_factory=clock.now),
            "notifications": notifications,
            "policy_config": policy,
            "current_user": HeaderCurrentUserProvider(),
            "admin_identity": ConfiguredAdminIdentityProvider(lambda: settings),
        }
        defaults.update(overrides)
        return build_container(**defaults)

    return _make


@pytest.fixture
def container(make_container):
    return make_container()


@pytest.fixture
def client(container) -> TestClient:
    return TestClient(create_app(container=container))


@pytest.fixture
def make_client(make_container):
    def _make(**overrides) -> TestClient:
        return TestClient(create_app(container=make_container(**overrides)))

    return _make
