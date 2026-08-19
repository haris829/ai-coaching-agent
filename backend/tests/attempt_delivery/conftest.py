"""Fixtures for the UC-03 suite.

Each test gets its own isolated database, a controllable clock and a controllable downstream
dispatcher. That combination is what lets the suite assert time-expiry and pending-submission
behaviour deterministically, with no sleeping and no flakiness.

UC-01 and UC-02 are supplied as **port fakes** (``tests.support.fakes``) rather than the real
adapters — see that module for why. The real adapters are covered by ``tests/integration/``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings as app_settings
from app.core.time import FixedClock
from app.db.session import create_db_engine, create_session_factory, get_db
from app.main import create_app
from app.modules.attempt_delivery.container import AppContext, Ports, RequestContext
from app.modules.attempt_delivery.integration.submission_dispatch.port import (
    SubmissionDispatchRequest,
    SubmissionDispatchResult,
    TransientDispatchError,
)
from app.modules.identity.enums import EnrolmentStatus
from app.modules.identity.models import Enrolment, User
from app.modules.identity.principal import Role
from tests.support.client import ApiClient
from tests.support.fakes import (
    FakeEnrolmentPort,
    FakeQuestionBankPort,
    FakeQuizConfigurationPort,
)
from tests.support.fixtures import COURSE_ID, LEARNER_ID, OTHER_LEARNER_ID

#: Development credentials for the two learners the suite acts as.
LEARNER_TOKEN = "uc03-learner-token"
OTHER_LEARNER_TOKEN = "uc03-other-learner-token"


class ControllableDispatch:
    """A downstream grading stub whose failure mode the test chooses.

    This is the seam that makes PENDING and FAILED submissions reachable without monkey-patching
    internals: the port is a real dependency, so the test simply supplies an implementation that
    fails the way it wants to exercise.
    """

    __slots__ = ("mode", "calls")

    def __init__(self) -> None:
        #: ``"ok"`` | ``"transient"`` | ``"permanent"``
        self.mode: str = "ok"
        self.calls: list[SubmissionDispatchRequest] = []

    def dispatch(self, request: SubmissionDispatchRequest) -> SubmissionDispatchResult:
        self.calls.append(request)
        if self.mode == "transient":
            raise TransientDispatchError("Simulated transient grading-service failure.")
        if self.mode == "permanent":
            raise RuntimeError("Simulated permanent grading-service failure.")
        return SubmissionDispatchResult(downstream_reference=f"grading-ref-{len(self.calls)}")

    # Convenience toggles, so the intent reads clearly at the call site.
    def fail_transiently(self) -> None:
        self.mode = "transient"

    def fail_permanently(self) -> None:
        self.mode = "permanent"

    def succeed(self) -> None:
        self.mode = "ok"


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock("2026-03-01T09:00:00Z")


@pytest.fixture
def dispatcher() -> ControllableDispatch:
    return ControllableDispatch()


@pytest.fixture
def configurations() -> FakeQuizConfigurationPort:
    return FakeQuizConfigurationPort()


@pytest.fixture
def question_bank() -> FakeQuestionBankPort:
    return FakeQuestionBankPort()


@pytest.fixture
def enrolments() -> FakeEnrolmentPort:
    return FakeEnrolmentPort()


@pytest.fixture
def settings():
    """UC-03's runtime knobs, with logging silenced so expected failures stay quiet."""
    return app_settings.model_copy(
        update={"error_logging": False, "submission_grace_seconds": 0}
    )


def build_context(
    *,
    settings: Any,
    clock: FixedClock,
    dispatcher: ControllableDispatch,
    configurations: FakeQuizConfigurationPort,
    question_bank: FakeQuestionBankPort,
    enrolments: FakeEnrolmentPort,
) -> AppContext:
    """An AppContext on its own in-memory database, with every boundary controlled.

    A dedicated engine (rather than the application's shared one) is what keeps a test that advances
    a clock or fails a dispatch from touching any other test's data.

    Exposed as a function, not only as a fixture, because a handful of tests need a *different*
    setting (a submission grace window, for instance) and must be able to build a context the same
    way rather than assembling a second, subtly different one by hand.
    """
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    app_context = AppContext(
        settings=settings,
        engine=engine,
        session_factory=create_session_factory(engine),
        clock=clock,
        # The fakes are per-test singletons, so seeding through the context and reading through a
        # request see the same state.
        ports=Ports(
            configurations=lambda _session: configurations,
            question_bank=lambda _session: question_bank,
            enrolments=lambda _session: enrolments,
            dispatcher=dispatcher,
        ),
    )
    app_context.create_schema()
    _seed_identities(app_context)
    return app_context


def build_client(context: AppContext) -> TestClient:
    """A TestClient whose identity lookups resolve against ``context``'s database.

    Identity resolves the bearer token through the ordinary ``get_db`` dependency, which points at
    the application's shared engine. This suite deliberately runs on a private in-memory engine, so
    the dependency is redirected to it — otherwise the seeded learners would be invisible and every
    request would 401.
    """
    app = create_app(attempt_context=context)

    def _db_from_context() -> Iterator[Any]:
        session = context.session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _db_from_context
    # raise_server_exceptions=False so the registered handlers produce real HTTP responses instead
    # of the exception propagating into the test.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def context(
    settings: Any,
    clock: FixedClock,
    dispatcher: ControllableDispatch,
    configurations: FakeQuizConfigurationPort,
    question_bank: FakeQuestionBankPort,
    enrolments: FakeEnrolmentPort,
) -> Iterator[AppContext]:
    app_context = build_context(
        settings=settings,
        clock=clock,
        dispatcher=dispatcher,
        configurations=configurations,
        question_bank=question_bank,
        enrolments=enrolments,
    )
    yield app_context
    app_context.dispose()


def _seed_identities(app_context: AppContext) -> None:
    """The two learners the suite acts as, plus their enrolments in the identity placeholder.

    UC-03 resolves the learner through the shared identity seam, so a bearer token has to map to a
    real row. The *enrolment rule* is still exercised through the fake port, which is what the
    eligibility tests manipulate.
    """
    with app_context.session_factory() as session:
        session.add_all(
            [
                User(
                    id=int(LEARNER_ID),
                    email="uc03-learner@test.local",
                    display_name="UC-03 Learner",
                    role=Role.LEARNER.value,
                    api_token=LEARNER_TOKEN,
                ),
                User(
                    id=int(OTHER_LEARNER_ID),
                    email="uc03-other@test.local",
                    display_name="UC-03 Other Learner",
                    role=Role.LEARNER.value,
                    api_token=OTHER_LEARNER_TOKEN,
                ),
                Enrolment(
                    learner_id=LEARNER_ID,
                    course_id=COURSE_ID,
                    status=EnrolmentStatus.ACTIVE.value,
                ),
            ]
        )
        session.commit()


@pytest.fixture
def unit_of_work(context: AppContext) -> Iterator[RequestContext]:
    """A standalone unit of work for seeding and direct service/repository assertions."""
    with context.unit_of_work() as ctx:
        yield ctx


@pytest.fixture
def client(context: AppContext) -> Iterator[TestClient]:
    with build_client(context) as test_client:
        yield test_client


#: Every learner the suite can authenticate as, so `api.as_learner(other)` needs no credential.
TOKENS = {LEARNER_ID: LEARNER_TOKEN, OTHER_LEARNER_ID: OTHER_LEARNER_TOKEN}


@pytest.fixture
def api(client: TestClient) -> ApiClient:
    return ApiClient(client, LEARNER_ID, LEARNER_TOKEN, tokens=TOKENS)


@pytest.fixture
def other_api(client: TestClient) -> ApiClient:
    """A second learner, for the ownership tests."""
    return ApiClient(client, OTHER_LEARNER_ID, OTHER_LEARNER_TOKEN, tokens=TOKENS)


@pytest.fixture
def seeded(context: AppContext) -> dict[str, Any]:
    """Seed the default world and return the configuration that was published."""
    from tests.support.fixtures import seed_world

    with context.unit_of_work() as ctx:
        configuration = seed_world(ctx)
    return {"configuration": configuration}
