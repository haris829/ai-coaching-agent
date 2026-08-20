"""Shared fixtures.

Every fixture is deterministic: a fixed clock, a fixed dataset and a stable id
factory, so failures are reproducible and CSV output can be compared byte for
byte.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.time import FixedClock
from app.modules.analytics.api.deps import build_container
from app.modules.analytics.cancellation import QueryContext
from app.modules.analytics.config import AnalyticsSettings
from app.modules.analytics.repositories.in_memory import (
    InMemoryAnalyticsRepository,
    InMemoryReviewRepository,
    InMemoryReviewStore,
)
from app.modules.analytics.services import (
    AnalyticsService,
    CsvExportService,
    FlagService,
    ReviewService,
)
from tests.analytics.factories import (
    BASE_TIME,
    NOW,
    make_attempt,
    make_question,
    make_response,
)
from tests.analytics.world import (
    ADMIN_PREFIX,
    admin_auth_headers,
    build_analytics_app,
)

ADMIN_ID = "admin-1"

#: A bearer token, not the ``X-API-Key`` header UC-10 shipped with: the merged application has one
#: authentication seam and UC-10 goes through it like every other capability.
AUTH_HEADERS = admin_auth_headers(ADMIN_ID)

#: Kept as a name because a few tests still refer to "the key"; it is now a token.
API_KEY = f"{ADMIN_PREFIX}{ADMIN_ID}"


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop host ANALYTICS_* variables from leaking into settings under test.

    The prefix changed with the merge — these are the application's settings now, named
    ``ANALYTICS_*`` alongside every other capability's — but the isolation matters for the same
    reason: a threshold set in a developer's shell must not decide what a test asserts.
    """
    for name in list(os.environ):
        if name.startswith(("UC10_", "ANALYTICS_")):
            monkeypatch.delenv(name, raising=False)


def make_settings(**overrides: Any) -> AnalyticsSettings:
    """Settings for tests: explicit values, no .env file, no host environment."""
    defaults: dict[str, Any] = {
        # ``admin_api_keys`` is gone: authentication is the application's, not a value a client
        # can validate candidate settings against.
        "flag_wrong_answer_rate_threshold": 40.0,
        "flag_min_responses": 3,
        "reflag_enabled": True,
        "reflag_min_new_responses": 3,
        "repository_page_size": 100,
        "query_timeout_seconds": 30.0,
        "decimal_places": 2,
    }
    defaults.update(overrides)
    return AnalyticsSettings(_env_file=None, **defaults)


@pytest.fixture
def anyio_backend() -> str:
    """What makes ``pytestmark = pytest.mark.anyio`` work in this package."""
    return "asyncio"


@pytest.fixture
def settings() -> AnalyticsSettings:
    return make_settings()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def context(clock: FixedClock) -> QueryContext:
    return QueryContext.create(timeout_seconds=30.0, clock=clock, request_id="test-request")


@pytest.fixture
def review_store() -> InMemoryReviewStore:
    return InMemoryReviewStore()


@pytest.fixture
def dataset() -> dict[str, list[Any]]:
    """A small, hand-checkable dataset.

    Deliberately mixed: two courses, two cohorts, both assessment types, an
    in-progress attempt, an abandoned attempt, an ungraded response, a skipped
    answer and a response with no timing data. Every metric below can be
    verified by hand from these records.

    Attempts
        a1 course-1 cohort-a quiz      COMPLETED   score 90 passed
        a2 course-1 cohort-a quiz      COMPLETED   score 40 failed
        a3 course-1 cohort-b formal    IN_PROGRESS no score
        a4 course-2 cohort-b formal    COMPLETED   score 60 passed
        a5 course-1 cohort-a quiz      ABANDONED   no score

    Responses on question-1: 1 correct, 3 incorrect (two chose "B"), 1 ungraded
    Responses on question-2: 1 correct, 1 skipped-and-ungraded
    """
    attempts = [
        make_attempt("a1", learner_id="l1", score=90.0, passed=True, started_at=BASE_TIME),
        make_attempt(
            "a2", learner_id="l1", score=40.0, passed=False, started_at=BASE_TIME + timedelta(days=1)
        ),
        make_attempt(
            "a3",
            learner_id="l2",
            cohort_id="cohort-b",
            assessment_type="FORMAL_ASSESSMENT",
            status="IN_PROGRESS",
            score=None,
            passed=None,
            started_at=BASE_TIME + timedelta(days=2),
        ),
        make_attempt(
            "a4",
            learner_id="l3",
            course_id="course-2",
            cohort_id="cohort-b",
            assessment_type="FORMAL_ASSESSMENT",
            score=60.0,
            passed=True,
            started_at=BASE_TIME + timedelta(days=3),
        ),
        make_attempt(
            "a5",
            learner_id="l4",
            status="ABANDONED",
            score=None,
            passed=None,
            started_at=BASE_TIME + timedelta(days=4),
        ),
    ]
    responses = [
        # question-1
        make_response("r1", attempt_id="a1", question_id="question-1", selected_answer="A", is_correct=True, time_spent_seconds=10.0),
        make_response("r2", attempt_id="a2", question_id="question-1", selected_answer="B", is_correct=False, time_spent_seconds=20.0),
        make_response("r3", attempt_id="a4", question_id="question-1", selected_answer="B", is_correct=False, time_spent_seconds=None),
        make_response("r4", attempt_id="a5", question_id="question-1", selected_answer="C", is_correct=False, time_spent_seconds=30.0),
        make_response("r5", attempt_id="a3", question_id="question-1", selected_answer=None, is_correct=None, time_spent_seconds=None),
        # question-2
        make_response("r6", attempt_id="a1", question_id="question-2", selected_answer="A", is_correct=True, time_spent_seconds=45.0),
        make_response("r7", attempt_id="a2", question_id="question-2", selected_answer=None, is_correct=None, time_spent_seconds=None),
    ]
    questions = [
        make_question("question-1", question_type="MULTIPLE_CHOICE"),
        make_question("question-2", question_type="TRUE_FALSE"),
    ]
    return {"attempts": attempts, "responses": responses, "questions": questions}


@pytest.fixture
def repository(
    dataset: dict[str, list[Any]], review_store: InMemoryReviewStore
) -> InMemoryAnalyticsRepository:
    return InMemoryAnalyticsRepository(
        dataset["attempts"],
        dataset["responses"],
        dataset["questions"],
        review_store=review_store,
    )


@pytest.fixture
def review_repository(review_store: InMemoryReviewStore) -> InMemoryReviewRepository:
    return InMemoryReviewRepository(review_store)


@pytest.fixture
def analytics_service(
    repository: InMemoryAnalyticsRepository,
    settings: AnalyticsSettings,
    clock: FixedClock,
) -> AnalyticsService:
    return AnalyticsService(repository, settings, clock)


@pytest.fixture
def flag_service(
    analytics_service: AnalyticsService,
    repository: InMemoryAnalyticsRepository,
    review_repository: InMemoryReviewRepository,
    settings: AnalyticsSettings,
    clock: FixedClock,
) -> FlagService:
    return FlagService(analytics_service, repository, review_repository, settings, clock)


@pytest.fixture
def action_ids() -> Iterator[str]:
    """Stable action ids, so audit assertions can name them."""

    def generator():
        index = 0
        while True:
            index += 1
            yield f"action-{index:03d}"

    return generator()


@pytest.fixture
def review_service(
    review_repository: InMemoryReviewRepository,
    settings: AnalyticsSettings,
    clock: FixedClock,
    action_ids: Iterator[str],
) -> ReviewService:
    return ReviewService(review_repository, settings, clock, id_factory=lambda: next(action_ids))


@pytest.fixture
def export_service(
    analytics_service: AnalyticsService,
    settings: AnalyticsSettings,
    clock: FixedClock,
) -> CsvExportService:
    return CsvExportService(analytics_service, settings, clock)


@pytest.fixture
def app(
    repository: InMemoryAnalyticsRepository,
    review_repository: InMemoryReviewRepository,
    settings: AnalyticsSettings,
    clock: FixedClock,
):
    container = build_container(
        analytics_repository=repository,
        review_repository=review_repository,
        settings=settings,
        clock=clock,
    )
    return build_analytics_app(container)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def api() -> str:
    """Where UC-10 lives in the merged application.

    Standalone it owned ``/api/v1``. Analytics is an administrator capability end to end, so it
    joins the admin surface — and ``/api/v1`` in this application is the *learner* conversation,
    which analytics has no business being addressable from.
    """
    return "/api/admin"
