"""Composition root.

Wiring lives here and nowhere else, which is what keeps every boundary swappable: the services
depend on the protocols in ``integration`` and ``repositories``, and the decision about which
implementation satisfies them is made at this single point.

When the branches are merged and the company systems are connected, **this file is the only one that
changes**:

* ``formal_attempts_repository`` / ``sessions_repository`` / ``reviews_repository``
                     -> the company database adapters (UC-09 still defines no schema);
* ``policies``       -> the real UC-01 module (its ``is_formal_assessment`` configuration flag);
* ``upstream``       -> the real UC-03 module (attempts, autosave, submission);
* ``scores``         -> UC-04;
* ``results``        -> UC-05's pass/fail;
* ``certificates``   -> UC-05's certificate workflow;
* ``profiles``       -> the platform's user directory;
* ``assessors``      -> the company's assessor register or role service;
* ``queue``          -> the company's queue infrastructure;
* ``notifier``       -> the company's notification infrastructure;
* ``audit``          -> the platform audit pipeline.

No domain rule, no service and no test of the formal-assessment logic changes with them.

THE UNCONFIGURED DEFAULTS
-------------------------
Every unbound port defaults to an implementation that **refuses or reports nothing**, never to a
stub that returns plausible data. The choice matters more here than in the sibling use cases,
because the plausible defaults would each disable a safety rule:

* a policy provider that said "yes, formal" for every quiz would gate quizzes nobody configured;
  one that said "not formal" would silently *skip* the gate. So it reports the quiz as unknown;
* a profile source returning a matching name would make identity confirmation a formality, so it
  itself unavailable, and identity confirmation cannot succeed;
* an assessor directory that authorised everybody would mean an unwired deployment could approve
  certificates. So it authorises nobody;
* a certificate workflow reporting success would tell learners about certificates the company never
  generated. So it fails, retriably, and the approval stands.

An unwired UC-09 therefore starts, serves, and refuses to do anything consequential — which is the
honest behaviour for a module whose job is to withhold certificates.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.time import Clock, SystemClock
from app.modules.formal_assessment.domain.errors import (
    AttemptDeliveryUnavailableError,
    CertificateWorkflowFailedError,
    LearnerProfileUnavailableError,
    ScoringUnavailableError,
)
from app.modules.formal_assessment.domain.identity import LearnerIdentityProfile
from app.modules.formal_assessment.ids import (
    IdGenerator,
    TokenGenerator,
    secure_token,
    uuid_generator,
)
from app.modules.formal_assessment.integration.adapters import (
    FormalPassFailAdapter,
    FormalPolicyAdapter,
    FormalScoringAdapter,
    PlatformAssessorDirectory,
    PlatformLearnerProfileAdapter,
)
from app.modules.formal_assessment.integration.assessors import Assessor, AssessorDirectory
from app.modules.formal_assessment.integration.audit import FormalAuditLog, LoggingFormalAuditLog
from app.modules.formal_assessment.integration.notification import (
    LearnerNotifier,
    LoggingLearnerNotifier,
)
from app.modules.formal_assessment.integration.profiles import LearnerProfileProvider
from app.modules.formal_assessment.integration.results import (
    AttemptScore,
    CertificateAcknowledgement,
    CertificateTrigger,
    CertificateWorkflow,
    PassFailResult,
    PassFailResultProvider,
    ScoringResultProvider,
)
from app.modules.formal_assessment.integration.review_queue import (
    InMemoryReviewQueue,
    ReviewQueuePublisher,
)
from app.modules.formal_assessment.integration.uc01 import (
    FormalAssessmentPolicy,
    FormalAssessmentPolicyProvider,
)
from app.modules.formal_assessment.integration.uc03 import (
    AnswerSubmission,
    AttemptContext,
    AttemptProvider,
    AutosavedState,
    AutosaveResult,
    CreateAttemptRequest,
    QuestionResponse,
    SubmissionRequest,
    SubmittedState,
)
from app.modules.formal_assessment.repositories.in_memory import (
    InMemoryDeviceSessionRepository,
    InMemoryFormalAttemptRepository,
    InMemoryFormalReviewRepository,
)
from app.modules.formal_assessment.repositories.protocols import (
    DeviceSessionRepository,
    FormalAttemptRepository,
    FormalReviewRepository,
)
from app.modules.formal_assessment.repositories.sqlalchemy import (
    SqlAlchemyDeviceSessionRepository,
    SqlAlchemyFormalAttemptRepository,
    SqlAlchemyFormalReviewRepository,
)
from app.modules.formal_assessment.services.certificate_service import FormalCertificateService
from app.modules.formal_assessment.services.coaching_policy_service import AiCoachingPolicyService
from app.modules.formal_assessment.services.conditions_service import FormalConditionsService
from app.modules.formal_assessment.services.device_session_service import DeviceSessionService
from app.modules.formal_assessment.services.formal_attempt_service import FormalAttemptService
from app.modules.formal_assessment.services.identity_service import FormalIdentityService
from app.modules.formal_assessment.services.policy_service import FormalPolicyService
from app.modules.formal_assessment.services.queue_recovery_service import (
    ReviewQueueRecoveryService,
)
from app.modules.formal_assessment.services.result_service import FormalResultService
from app.modules.formal_assessment.services.review_service import FormalReviewService


class UnconfiguredPolicyProvider:
    """No UC-01 module is bound yet. Every quiz reads as unknown, so no formal attempt is started.
    """

    async def get_policy(self, quiz_id: str) -> FormalAssessmentPolicy | None:
        return None

    async def get_policy_for_attempt(self, attempt_id: str) -> FormalAssessmentPolicy | None:
        return None


class UnconfiguredAttemptProvider:
    """No UC-03 module is bound yet.

    Reads report nothing; writes refuse with a retryable provider error rather than inventing an
    attempt or reporting a submission that never happened.
    """

    async def get_attempt(self, attempt_id: str) -> AttemptContext | None:
        return None

    async def find_open_attempt(self, learner_id: str, quiz_id: str) -> AttemptContext | None:
        return None

    async def create_attempt(self, request: CreateAttemptRequest) -> AttemptContext:
        raise AttemptDeliveryUnavailableError(
            "No attempt delivery module is bound, so a formal attempt cannot be created."
        )

    async def get_latest_autosaved_state(self, attempt_id: str) -> AutosavedState | None:
        return None

    async def save_answers(
        self, attempt_id: str, answers: tuple[AnswerSubmission, ...]
    ) -> AutosaveResult:
        raise AttemptDeliveryUnavailableError(
            "No attempt delivery module is bound, so answers cannot be saved."
        )

    async def submit_attempt(self, request: SubmissionRequest) -> SubmittedState:
        raise AttemptDeliveryUnavailableError(
            "No attempt delivery module is bound, so the attempt cannot be submitted."
        )

    async def get_attempt_responses(self, attempt_id: str) -> tuple[QuestionResponse, ...]:
        return ()


class UnconfiguredProfileProvider:
    """No profile source is bound.

    Reports itself unavailable rather than returning ``None``. A missing profile would read as "this
    learner does not exist"; unavailable is the truth, and it means identity confirmation cannot
    succeed by accident.
    """

    async def get_profile(self, learner_id: str) -> LearnerIdentityProfile | None:
        raise LearnerProfileUnavailableError(
            "No learner profile source is bound, so identity cannot be confirmed."
        )


class UnconfiguredScoringProvider:
    """No UC-04 module is bound. Results never resolve, so nothing passes and nothing is certified.
    """

    async def get_score(self, attempt_id: str) -> AttemptScore | None:
        return None


class UnconfiguredPassFailProvider:
    async def get_result(self, attempt_id: str) -> PassFailResult | None:
        return None


class UnconfiguredAssessorDirectory:
    """No assessor register is bound, so **nobody** is authorised to approve anything.

    The most important default in this file. A directory that authorised every caller would make the
    certificate gate a formality in exactly the deployment least likely to notice.
    """

    async def get_assessor(self, assessor_id: str) -> Assessor | None:
        return None

    async def list_authorised_course_ids(self, assessor_id: str) -> tuple[str, ...]:
        return ()


class UnconfiguredCertificateWorkflow:
    """No certificate workflow is bound.

    Deliberately *not* a stub that returns success: reporting a certificate the company never
    generated would be worse than reporting none. It fails retriably, the approval stands, and the
    trigger can be repeated once UC-05's workflow is wired in.
    """

    async def trigger(self, request: CertificateTrigger) -> CertificateAcknowledgement:
        raise CertificateWorkflowFailedError(
            "No certificate workflow is bound for this deployment. The approval stands and the "
            "certificate can be requested once it is."
        )


def _unconfigured_scoring_error() -> ScoringUnavailableError:  # pragma: no cover - documentation
    """The error an adapter should raise when UC-04/UC-05 are unreachable, as opposed to undecided.
    """
    return ScoringUnavailableError("The scoring modules could not be reached.")


@dataclass
class Ports:
    policies: FormalAssessmentPolicyProvider
    upstream: AttemptProvider
    profiles: LearnerProfileProvider
    scores: ScoringResultProvider
    results: PassFailResultProvider
    assessors: AssessorDirectory
    certificates: CertificateWorkflow
    queue: ReviewQueuePublisher
    notifier: LearnerNotifier
    audit: FormalAuditLog


@dataclass
class Repositories:
    formal_attempts: FormalAttemptRepository
    sessions: DeviceSessionRepository
    reviews: FormalReviewRepository


@dataclass
class Services:
    policies: FormalPolicyService
    conditions: FormalConditionsService
    identity: FormalIdentityService
    sessions: DeviceSessionService
    reviews: FormalReviewService
    results: FormalResultService
    attempts: FormalAttemptService
    certificates: FormalCertificateService
    coaching: AiCoachingPolicyService
    recovery: ReviewQueueRecoveryService


@dataclass
class Container:
    settings: Settings
    clock: Clock
    new_id: IdGenerator
    new_token: TokenGenerator
    ports: Ports
    repositories: Repositories
    services: Services


def create_container(
    *,
    settings: Settings | None = None,
    clock: Clock | None = None,
    new_id: IdGenerator | None = None,
    new_token: TokenGenerator | None = None,
    policies: FormalAssessmentPolicyProvider | None = None,
    upstream: AttemptProvider | None = None,
    profiles: LearnerProfileProvider | None = None,
    scores: ScoringResultProvider | None = None,
    results: PassFailResultProvider | None = None,
    assessors: AssessorDirectory | None = None,
    certificates: CertificateWorkflow | None = None,
    queue: ReviewQueuePublisher | None = None,
    notifier: LearnerNotifier | None = None,
    audit: FormalAuditLog | None = None,
    formal_attempts_repository: FormalAttemptRepository | None = None,
    sessions_repository: DeviceSessionRepository | None = None,
    reviews_repository: FormalReviewRepository | None = None,
) -> Container:
    """Build the module. Every dependency is overridable, which is how tests inject fakes."""
    config = settings or default_settings
    the_clock = clock or SystemClock()
    ids = new_id or uuid_generator
    tokens = new_token or secure_token

    ports = Ports(
        policies=policies or UnconfiguredPolicyProvider(),
        upstream=upstream or UnconfiguredAttemptProvider(),
        profiles=profiles or UnconfiguredProfileProvider(),
        scores=scores or UnconfiguredScoringProvider(),
        results=results or UnconfiguredPassFailProvider(),
        assessors=assessors or UnconfiguredAssessorDirectory(),
        certificates=certificates or UnconfiguredCertificateWorkflow(),
        queue=queue or InMemoryReviewQueue(),
        notifier=notifier or LoggingLearnerNotifier(),
        audit=audit or LoggingFormalAuditLog(),
    )

    repositories = Repositories(
        formal_attempts=formal_attempts_repository or InMemoryFormalAttemptRepository(),
        sessions=sessions_repository or InMemoryDeviceSessionRepository(),
        reviews=reviews_repository or InMemoryFormalReviewRepository(),
    )

    policy_service = FormalPolicyService(policies=ports.policies)

    conditions_service = FormalConditionsService(
        attempts=repositories.formal_attempts,
        policies=policy_service,
        audit=ports.audit,
        clock=the_clock,
        new_id=ids,
        conditions_version=config.formal_conditions_version,
    )

    identity_service = FormalIdentityService(
        attempts=repositories.formal_attempts,
        profiles=ports.profiles,
        conditions=conditions_service,
        audit=ports.audit,
        clock=the_clock,
    )

    session_service = DeviceSessionService(
        sessions=repositories.sessions,
        attempts=repositories.formal_attempts,
        audit=ports.audit,
        clock=the_clock,
        new_id=ids,
        new_token=tokens,
        heartbeat_timeout_seconds=config.session_heartbeat_timeout_seconds,
    )

    review_service = FormalReviewService(
        reviews=repositories.reviews,
        attempts=repositories.formal_attempts,
        sessions=repositories.sessions,
        upstream=ports.upstream,
        profiles=ports.profiles,
        assessors=ports.assessors,
        queue=ports.queue,
        notifier=ports.notifier,
        audit=ports.audit,
        clock=the_clock,
        new_id=ids,
        max_publish_attempts=config.review_queue_max_publish_attempts,
    )

    certificate_service = FormalCertificateService(
        attempts=repositories.formal_attempts,
        reviews=repositories.reviews,
        workflow=ports.certificates,
        audit=ports.audit,
        clock=the_clock,
    )
    # The one late binding: an approval triggers the certificate workflow, and the certificate gate
    # reads the
    # review. Wiring it here rather than constructing one inside the other keeps both independently
    # testable.
    review_service.bind_certificates(certificate_service)

    result_service = FormalResultService(
        attempts=repositories.formal_attempts,
        scores=ports.scores,
        results=ports.results,
        reviews=review_service,
        audit=ports.audit,
        clock=the_clock,
    )

    attempt_service = FormalAttemptService(
        attempts=repositories.formal_attempts,
        upstream=ports.upstream,
        policies=policy_service,
        conditions=conditions_service,
        identity=identity_service,
        sessions=session_service,
        results=result_service,
        audit=ports.audit,
        clock=the_clock,
    )

    coaching_service = AiCoachingPolicyService(
        attempts=repositories.formal_attempts,
        audit=ports.audit,
        clock=the_clock,
    )

    recovery_service = ReviewQueueRecoveryService(reviews=review_service)

    return Container(
        settings=config,
        clock=the_clock,
        new_id=ids,
        new_token=tokens,
        ports=ports,
        repositories=repositories,
        services=Services(
            policies=policy_service,
            conditions=conditions_service,
            identity=identity_service,
            sessions=session_service,
            reviews=review_service,
            results=result_service,
            attempts=attempt_service,
            certificates=certificate_service,
            coaching=coaching_service,
            recovery=recovery_service,
        ),
    )


# ---------------------------------------------------------------------------
# The merged application's wiring
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FormalPorts:
    """The boundaries that need a database session, as per-session factories.

    Factories rather than instances, for the reason every other capability's context does the
    same: a SQLAlchemy session is not thread-safe, so each unit of work gets adapters bound to its
    own.

    ``upstream`` and ``certificates`` have no defaults and must be supplied. Both need a fully
    wired capability rather than merely a database handle — UC-03's attempt service and UC-05's
    certification service — and naming those capabilities here would be a cross-capability import
    in a composition root, which ``tests/test_architecture.py`` forbids. The two
    ``*_factory`` helpers in ``integration/`` build them, and the application factory passes them
    in.
    """

    policies: Callable[[Session], FormalAssessmentPolicyProvider]
    upstream: Callable[[Session], AttemptProvider]
    profiles: Callable[[Session], LearnerProfileProvider]
    scores: Callable[[Session], ScoringResultProvider]
    results: Callable[[Session], PassFailResultProvider]
    assessors: Callable[[Session], AssessorDirectory]
    certificates: Callable[[Session], CertificateWorkflow]
    formal_attempts: Callable[[Session], FormalAttemptRepository]
    sessions: Callable[[Session], DeviceSessionRepository]
    reviews: Callable[[Session], FormalReviewRepository]

    @classmethod
    def merged(
        cls,
        upstream: Callable[[Session], AttemptProvider],
        certificates: Callable[[Session], CertificateWorkflow],
    ) -> FormalPorts:
        """The real adapters: UC-01, UC-03, UC-04, UC-05, the platform directory and ``qs_``.

        This one call is the whole of the integration. Substituting the company's assessor
        register, notification channel or audit pipeline is a change to the line that names it.
        """
        return cls(
            policies=FormalPolicyAdapter,
            upstream=upstream,
            profiles=PlatformLearnerProfileAdapter,
            scores=FormalScoringAdapter,
            results=FormalPassFailAdapter,
            assessors=PlatformAssessorDirectory,
            certificates=certificates,
            formal_attempts=SqlAlchemyFormalAttemptRepository,
            sessions=SqlAlchemyDeviceSessionRepository,
            reviews=SqlAlchemyFormalReviewRepository,
        )


class FormalAssessmentAppContext:
    """Process-wide dependencies for UC-09, and the factory for one request's services.

    The counterpart of ``ResultsAppContext``, ``CoachingAppContext`` and ``RetakeAppContext``. It
    holds what outlives a request — settings, clock, id and token generators, the queue, the
    notifier and the audit sink — and builds a :class:`Container` per session on top of them.

    The **queue is process-wide and deliberately not durable**. That is not an oversight: the
    ``PENDING_REVIEW`` record is persisted *before* the queue is touched, so a queue that loses
    everything loses notifications, and every review is still listed, still reviewable, and still
    blocking its certificate. Durability is not what makes the queue-outage requirement hold.
    """

    __slots__ = (
        "session_factory",
        "settings",
        "clock",
        "new_id",
        "new_token",
        "queue",
        "notifier",
        "audit",
        "ports",
    )

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        upstream: Callable[[Session], AttemptProvider],
        certificates: Callable[[Session], CertificateWorkflow],
        settings: Settings | None = None,
        clock: Clock | None = None,
        new_id: IdGenerator | None = None,
        new_token: TokenGenerator | None = None,
        queue: ReviewQueuePublisher | None = None,
        notifier: LearnerNotifier | None = None,
        audit: FormalAuditLog | None = None,
        ports: FormalPorts | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or default_settings
        self.clock = clock or SystemClock()
        self.new_id = new_id or uuid_generator
        self.new_token = new_token or secure_token
        self.queue = queue or InMemoryReviewQueue()
        self.notifier = notifier or LoggingLearnerNotifier()
        self.audit = audit or LoggingFormalAuditLog()
        self.ports = ports or FormalPorts.merged(upstream, certificates)

    def build(self, session: Session) -> Container:
        """Assemble UC-09's services for one session."""
        return create_container(
            settings=self.settings,
            clock=self.clock,
            new_id=self.new_id,
            new_token=self.new_token,
            queue=self.queue,
            notifier=self.notifier,
            audit=self.audit,
            policies=self.ports.policies(session),
            upstream=self.ports.upstream(session),
            profiles=self.ports.profiles(session),
            scores=self.ports.scores(session),
            results=self.ports.results(session),
            assessors=self.ports.assessors(session),
            certificates=self.ports.certificates(session),
            formal_attempts_repository=self.ports.formal_attempts(session),
            sessions_repository=self.ports.sessions(session),
            reviews_repository=self.ports.reviews(session),
        )

    @contextmanager
    def unit_of_work(self) -> Iterator[Container]:
        """A standalone unit of work, for scripts and tests."""
        session = self.session_factory()
        try:
            yield self.build(session)
        finally:
            session.close()
