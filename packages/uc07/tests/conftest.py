"""Shared test helpers. Everything is deterministic: fixed clock, fixed data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uc07.adapters.clock import FixedClock
from uc07.adapters.mock import MockScenario, get_scenario, providers_for
from uc07.adapters.mock.scenarios import LEARNER, OTHER_LEARNER
from uc07.adapters.persistence import InMemoryGapReportRepository
from uc07.application.config import AnalysisThresholds, Settings
from uc07.application.service import GapReportService
from uc07.application.topic_descriptions import TopicDescriptionRegistry
from uc07.composition import Container
from uc07.ports import Clock, GapReportRepository

FIXED_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)

REGISTRY_PATH = Path("uc07/config/topic_descriptions.json")

DEFAULT_THRESHOLDS = AnalysisThresholds(
    gap_report_threshold=10,
    min_topic_areas=3,
    explain_differently_struggle_threshold=2,
    low_rating_struggle_threshold=1,
    follow_up_struggle_threshold=2,
)


@dataclass(frozen=True, slots=True)
class Harness:
    """A wired service plus the collaborators tests want to inspect."""

    service: GapReportService
    repository: InMemoryGapReportRepository
    scenario: MockScenario
    user_id: str


def registry() -> TopicDescriptionRegistry:
    return TopicDescriptionRegistry.from_path(REGISTRY_PATH)


def build_harness(
    scenario: MockScenario | str,
    *,
    repository: GapReportRepository | None = None,
    clock: Clock | None = None,
    thresholds: AnalysisThresholds | None = None,
) -> Harness:
    resolved = get_scenario(scenario) if isinstance(scenario, str) else scenario
    providers = providers_for(resolved)
    repo = repository or InMemoryGapReportRepository()
    service = GapReportService(
        interactions=providers.interactions,
        feedback=providers.feedback,
        profiles=providers.profiles,
        courses=providers.courses,
        repository=repo,
        clock=clock or FixedClock(FIXED_NOW),
        descriptions=registry(),
        thresholds=thresholds or DEFAULT_THRESHOLDS,
    )
    return Harness(
        service=service, repository=repo, scenario=resolved, user_id=resolved.user_id
    )


def build_client(
    scenario: MockScenario | str = "struggle_mixed",
    *,
    clock: Clock | None = None,
    thresholds: AnalysisThresholds | None = None,
    repository: GapReportRepository | None = None,
) -> TestClient:
    """A TestClient over the real app, wired to a chosen mock scenario."""
    from uc07.adapters.identity import HeaderCurrentUserProvider
    from uc07.api.app import create_app

    harness = build_harness(
        scenario, clock=clock, thresholds=thresholds, repository=repository
    )
    container = Container(
        settings=Settings(),
        service=harness.service,
        current_user=HeaderCurrentUserProvider("X-User-Id"),
        repository=harness.repository,
        clock=clock or FixedClock(FIXED_NOW),
    )
    return TestClient(create_app(container), raise_server_exceptions=False)


def auth(user_id: str = LEARNER) -> dict[str, str]:
    return {"X-User-Id": user_id}


@pytest.fixture
def learner() -> str:
    return LEARNER


@pytest.fixture
def other_learner() -> str:
    return OTHER_LEARNER
