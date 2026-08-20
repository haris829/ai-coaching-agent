"""Fixtures for UC-08's suite.

Every test builds the module through ``create_container`` with fakes injected, which is the same
entry point the application uses. Nothing reaches into a service's internals, so a refactor that
keeps the behaviour keeps the tests.

``FixedClock`` and ``SequentialIdGenerator`` make timestamps and ids assertable with plain
equality — no ``mock.ANY`` and no regular expressions over generated values.

**These tests run against port fakes, not the real UC-01…UC-07 adapters**, and deliberately so —
the same decision UC-03's and UC-07's suites document. Most of what UC-08 has to get right is what
it does when an upstream call *fails*, and several required behaviours are unreachable through the
real chain: UC-01 will not publish a configuration whose bank cannot fill a paper, so "the bank is
too small and reuse is unavoidable" cannot be set up through it; a real UC-03 cannot be made to
time out on the second of two concurrent reservations. ``FakeAttemptModule`` implements UC-03's
*contract* — including the unseen-first selection ordering and both uniqueness invariants — rather
than returning whatever UC-08 asked for, so these tests exercise the contract and not a helpful
double. The real adapters are covered by ``tests/integration/test_retake_chain.py``, which drives
the whole thing over HTTP against a real database.

The root ``conftest.py``'s ``_clean_tables`` fixture still applies and is harmless here: nothing in
this package touches the database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.time import FixedClock
from app.modules.retakes.container import Container, create_container
from tests.retakes.fakes import (
    DEFAULT_COURSE,
    DEFAULT_LEARNER,
    DEFAULT_QUIZ,
    FakeAttemptModule,
    FakeCoachingProvider,
    FakeConfigurationProvider,
    FakeFeedbackProvider,
    FakePassFailProvider,
    FakeQuestionBank,
    FakeScoringProvider,
    RecordingAuditLog,
)
from tests.retakes.world import (
    SequentialIdGenerator,
    build_retake_app,
    learner_auth_headers,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock("2026-01-02T10:00:00+00:00")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        admin_api_token="",
        retake_configuration_policy="ACTIVE_AT_RETAKE",
        max_grant_additional_attempts=10,
    )


@pytest.fixture
def configurations() -> FakeConfigurationProvider:
    return FakeConfigurationProvider()


@pytest.fixture
def bank() -> FakeQuestionBank:
    return FakeQuestionBank()


@pytest.fixture
def attempts(
    configurations: FakeConfigurationProvider, bank: FakeQuestionBank
) -> FakeAttemptModule:
    return FakeAttemptModule(configurations=configurations, bank=bank)


@pytest.fixture
def scores() -> FakeScoringProvider:
    return FakeScoringProvider()


@pytest.fixture
def results() -> FakePassFailProvider:
    return FakePassFailProvider()


@pytest.fixture
def feedback() -> FakeFeedbackProvider:
    return FakeFeedbackProvider()


@pytest.fixture
def coaching() -> FakeCoachingProvider:
    return FakeCoachingProvider()


@pytest.fixture
def audit() -> RecordingAuditLog:
    return RecordingAuditLog()


@pytest.fixture
def container(
    settings: Settings,
    clock: FixedClock,
    configurations: FakeConfigurationProvider,
    bank: FakeQuestionBank,
    attempts: FakeAttemptModule,
    scores: FakeScoringProvider,
    results: FakePassFailProvider,
    feedback: FakeFeedbackProvider,
    coaching: FakeCoachingProvider,
    audit: RecordingAuditLog,
) -> Container:
    return create_container(
        settings=settings,
        clock=clock,
        new_id=SequentialIdGenerator("uc08"),
        configurations=configurations,
        question_bank=bank,
        attempts=attempts,
        scores=scores,
        results=results,
        feedback=feedback,
        coaching=coaching,
        audit=audit,
    )


@pytest.fixture
def quiz(configurations: FakeConfigurationProvider, bank: FakeQuestionBank):
    """A published quiz with a ten-question bank and a maximum of two attempts.

    The default shape for most tests: enough questions that a three-question paper can be entirely
    fresh twice over, so a test that sees reuse is seeing a real finding.
    """
    config = configurations.publish(question_count=3, maximum_attempts=2)
    bank.add_many(10)
    return config


@pytest.fixture
def first_attempt(quiz, attempts: FakeAttemptModule):
    """One submitted attempt at the quiz, delivering q1–q3."""
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)
    return attempts.attempts[attempt.attempt_id].context


@pytest.fixture
async def client(container: Container, anyio_backend: str) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to an app whose UC-08 context serves this test's container.

    Standalone, UC-08's ``create_app`` took the container directly. The merged application factory
    takes a :class:`RetakeAppContext`, so ``build_retake_app`` wraps the test container in one that
    returns it for every session — see ``world.py``.
    """
    app = build_retake_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def learner_headers() -> dict[str, str]:
    """Authentication for the default learner.

    A bearer token, not the ``X-Learner-Id`` header UC-08 shipped with: the merged application has
    one authentication seam and UC-08 goes through it like every other capability.
    """
    return learner_auth_headers(DEFAULT_LEARNER)


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"X-Admin-User": "admin-jo"}


@pytest.fixture
def ids() -> Iterator[tuple[str, str, str]]:
    yield DEFAULT_LEARNER, DEFAULT_COURSE, DEFAULT_QUIZ
