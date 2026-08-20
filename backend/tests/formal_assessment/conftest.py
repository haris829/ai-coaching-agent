"""Shared fixtures.

Every test builds the module through ``create_container`` with fakes injected, which is the same entry point the
application uses. Nothing reaches into a service's internals, so a refactor that keeps the behaviour keeps the
tests.

``FixedClock`` and ``SequentialIdGenerator`` make timestamps and ids assertable with plain equality — no
``mock.ANY`` and no regular expressions over generated values. The session token generator is sequential for the
same reason; production uses ``secrets``.

``formal_flow`` is the fixture most tests start from: it walks a learner through acknowledge → confirm identity →
start, so a test about submission or review does not restate the four steps before it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.time import FixedClock
from app.modules.formal_assessment.container import Container, create_container
from app.modules.formal_assessment.domain.conditions import REQUIRED_CONDITION_CODES
from app.modules.formal_assessment.domain.device import DeviceDescriptor
from app.modules.formal_assessment.domain.identity import IdentitySubmission
from app.modules.formal_assessment.ids import SequentialIdGenerator, SequentialTokenGenerator
from app.modules.formal_assessment.integration.review_queue import InMemoryReviewQueue
from tests.formal_assessment.fakes import (
    DEFAULT_ASSESSOR,
    DEFAULT_COURSE,
    DEFAULT_LEARNER,
    DEFAULT_NAME,
    DEFAULT_QUIZ,
    FakeAssessorDirectory,
    FakeAttemptModule,
    FakeCertificateWorkflow,
    FakePassFailProvider,
    FakePolicyProvider,
    FakeProfileProvider,
    FakeScoringProvider,
    RecordingAuditLog,
    RecordingNotifier,
)
from tests.formal_assessment.world import (
    assessor_auth_headers,
    build_formal_app,
    learner_auth_headers,
    system_auth_headers,
)

#: Every required condition code, as a client would send them.
ALL_CONDITION_CODES = sorted(code.value for code in REQUIRED_CONDITION_CODES)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock("2026-03-01T09:00:00.000Z")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        admin_api_token="",
        assessor_api_token="",
        system_api_token="",
        formal_conditions_version="2026.1",
        review_queue_max_publish_attempts=3,
        session_heartbeat_timeout_seconds=90,
    )


@pytest.fixture
def policies() -> FakePolicyProvider:
    provider = FakePolicyProvider()
    provider.publish()
    return provider


@pytest.fixture
def upstream() -> FakeAttemptModule:
    return FakeAttemptModule()


@pytest.fixture
def profiles() -> FakeProfileProvider:
    provider = FakeProfileProvider()
    provider.add()
    return provider


@pytest.fixture
def scores() -> FakeScoringProvider:
    return FakeScoringProvider()


@pytest.fixture
def results() -> FakePassFailProvider:
    return FakePassFailProvider()


@pytest.fixture
def assessors() -> FakeAssessorDirectory:
    directory = FakeAssessorDirectory()
    directory.add()
    return directory


@pytest.fixture
def certificates() -> FakeCertificateWorkflow:
    return FakeCertificateWorkflow()


@pytest.fixture
def queue() -> InMemoryReviewQueue:
    return InMemoryReviewQueue()


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def audit() -> RecordingAuditLog:
    return RecordingAuditLog()


@pytest.fixture
def container(
    settings: Settings,
    clock: FixedClock,
    policies: FakePolicyProvider,
    upstream: FakeAttemptModule,
    profiles: FakeProfileProvider,
    scores: FakeScoringProvider,
    results: FakePassFailProvider,
    assessors: FakeAssessorDirectory,
    certificates: FakeCertificateWorkflow,
    queue: InMemoryReviewQueue,
    notifier: RecordingNotifier,
    audit: RecordingAuditLog,
) -> Container:
    return create_container(
        settings=settings,
        clock=clock,
        new_id=SequentialIdGenerator("uc09"),
        new_token=SequentialTokenGenerator("token"),
        policies=policies,
        upstream=upstream,
        profiles=profiles,
        scores=scores,
        results=results,
        assessors=assessors,
        certificates=certificates,
        queue=queue,
        notifier=notifier,
        audit=audit,
    )


@pytest.fixture
async def client(container: Container, anyio_backend: str) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to an app whose UC-09 context serves this test's container.

    Standalone, UC-09's ``create_app`` took the container directly. The merged factory takes a
    :class:`FormalAssessmentAppContext`, so ``build_formal_app`` wraps the test container in one
    that returns it for every session — see ``world.py``.
    """
    app = build_formal_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def learner_headers() -> dict[str, str]:
    """Credentials for the default learner.

    A bearer token, not the ``X-Learner-Id`` header UC-09 shipped with: the merged application has
    one authentication seam and UC-09 goes through it like every other capability. The same is
    true of the assessor and system credentials below, which is what makes "a learner cannot reach
    a system endpoint" a real assertion rather than a matter of which header was sent.
    """
    return learner_auth_headers(DEFAULT_LEARNER)


@pytest.fixture
def assessor_headers() -> dict[str, str]:
    return assessor_auth_headers(DEFAULT_ASSESSOR)


@pytest.fixture
def system_headers() -> dict[str, str]:
    return system_auth_headers("session-monitor")


@pytest.fixture
def ids() -> tuple[str, str, str]:
    return DEFAULT_LEARNER, DEFAULT_COURSE, DEFAULT_QUIZ


#: The learner half joins UC-03's versioned conversation; the assessor and system halves get their
#: own roots, because they carry different credentials.
API = "/api/v1"
ASSESSOR_API = "/api/assessor"
SYSTEM_API = "/api/system/formal-assessments"


@dataclass
class FormalFlow:
    """A learner walked to a chosen point in the formal lifecycle, for tests that start further in."""

    container: Container
    learner_id: str = DEFAULT_LEARNER
    quiz_id: str = DEFAULT_QUIZ
    formal_attempt_id: str = ""
    attempt_id: str = ""
    session_token: str = ""

    async def acknowledge(self) -> Any:
        outcome = await self.container.services.conditions.acknowledge(
            learner_id=self.learner_id,
            quiz_id=self.quiz_id,
            acknowledged_codes=ALL_CONDITION_CODES,
            user_agent="pytest",
        )
        self.formal_attempt_id = outcome.formal_attempt.formal_attempt_id
        return outcome.formal_attempt

    async def confirm_identity(self, name: str = DEFAULT_NAME, email: str | None = None) -> Any:
        outcome = await self.container.services.identity.confirm(
            learner_id=self.learner_id,
            quiz_id=self.quiz_id,
            submission=IdentitySubmission(full_name=name, email=email),
        )
        return outcome.formal_attempt

    async def start(
        self, *, fingerprint: str = "device-a", client_request_id: str | None = None
    ) -> Any:
        outcome = await self.container.services.attempts.start(
            learner_id=self.learner_id,
            quiz_id=self.quiz_id,
            device=DeviceDescriptor(fingerprint=fingerprint, user_agent="pytest"),
            client_request_id=client_request_id,
        )
        self.formal_attempt_id = outcome.formal_attempt.formal_attempt_id
        self.attempt_id = outcome.formal_attempt.attempt_id or ""
        self.session_token = outcome.session.session_token
        return outcome

    async def to_active(self) -> Any:
        await self.acknowledge()
        await self.confirm_identity()
        return await self.start()

    async def submit(self) -> Any:
        return await self.container.services.attempts.submit(
            learner_id=self.learner_id,
            formal_attempt_id=self.formal_attempt_id,
            session_token=self.session_token,
        )

    async def record(self) -> Any:
        return await self.container.services.attempts.get_owned(
            self.learner_id, self.formal_attempt_id
        )

    async def review(self) -> Any:
        return await self.container.services.reviews.get_for_formal_attempt(self.formal_attempt_id)


@pytest.fixture
def flow(container: Container) -> FormalFlow:
    return FormalFlow(container=container)


@pytest.fixture
def passing(scores: FakeScoringProvider, results: FakePassFailProvider) -> Any:
    """Arrange a confirmed, passing score for whichever attempt a test creates.

    Returned as a callable rather than applied eagerly, because the attempt id does not exist until the test
    starts the attempt.
    """

    def arrange(attempt_id: str, *, passed: bool = True, percentage: float = 90.0) -> None:
        scores.record(attempt_id, percentage=percentage)
        results.record(
            attempt_id,
            status="PASSED" if passed else "FAILED",
            percentage=percentage,
            pass_mark=80.0,
        )

    return arrange
