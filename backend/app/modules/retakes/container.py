"""Composition root.

Wiring lives here and nowhere else, which is what keeps every boundary swappable: the services
depend on the protocols in ``integration`` and ``repositories``, and the decision about which
implementation satisfies them is made at this single point.

When the branches are merged and the company systems are connected, **this file is the only one
that changes**:

* ``retakes_repository`` / ``grants_repository`` → the company database adapters
  (UC-08 still defines no schema);
* ``configurations`` → the real UC-01 module;
* ``question_bank`` → the real UC-02 module;
* ``attempts``      → the real UC-03 module;
* ``scores``        → UC-04;
* ``results``       → UC-05;
* ``feedback``      → UC-06;
* ``coaching``      → UC-07;
* ``audit``         → the platform audit pipeline.

No domain rule, no service and no test of the retake logic changes with them.

THE UNCONFIGURED DEFAULTS
-------------------------
Every unbound port defaults to an implementation that reports nothing rather than a stub that
returns plausible data. A fake configuration provider would let a retake be created against an
attempt limit nobody set; a question bank that returned an empty pool instead of reporting itself
unavailable would be indistinguishable from a bank with no alternatives, and would quietly deliver
the learner the same paper again. So an unwired deployment answers "this quiz does not exist" and
"the question bank is unavailable", which are the truth.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.errors import ProviderUnavailableError
from app.core.time import Clock, SystemClock
from app.modules.retakes.domain.errors import QuestionBankUnavailableError
from app.modules.retakes.ids import IdGenerator, uuid_generator
from app.modules.retakes.integration.audit import LoggingRetakeAuditLog, RetakeAuditLog
from app.modules.retakes.integration.downstream import (
    AttemptScore,
    CoachingAvailability,
    CoachingProvider,
    FeedbackAvailability,
    FeedbackProvider,
    PassFailResult,
    PassFailResultProvider,
    ScoringResultProvider,
)
from app.modules.retakes.integration.downstream_adapters import (
    RetakeCoachingAdapter,
    RetakeFeedbackAdapter,
    RetakePassFailAdapter,
    RetakeScoringAdapter,
)
from app.modules.retakes.integration.uc01 import (
    ConfigurationProvider,
    QuizAvailability,
    QuizConfigurationVersion,
)
from app.modules.retakes.integration.uc01_adapter import RetakeConfigurationAdapter
from app.modules.retakes.integration.uc02 import (
    QuestionBankProvider,
    QuestionDescriptor,
    QuestionPoolQuery,
)
from app.modules.retakes.integration.uc02_adapter import RetakeQuestionBankAdapter
from app.modules.retakes.integration.uc03 import (
    AttemptContext,
    AttemptProvider,
    DeliveredAttempt,
    RetakeAttemptRequest,
)
from app.modules.retakes.repositories.in_memory import (
    InMemoryGrantRepository,
    InMemoryRetakeRequestRepository,
)
from app.modules.retakes.repositories.protocols import GrantRepository, RetakeRequestRepository
from app.modules.retakes.repositories.sqlalchemy import (
    SqlAlchemyGrantRepository,
    SqlAlchemyRetakeRequestRepository,
)
from app.modules.retakes.services.allowance_service import AttemptAllowanceService
from app.modules.retakes.services.eligibility_service import RetakeEligibilityService
from app.modules.retakes.services.grant_service import GrantService
from app.modules.retakes.services.history_service import AttemptHistoryService
from app.modules.retakes.services.question_plan_service import RetakeQuestionPlanService
from app.modules.retakes.services.retake_service import RetakeService


class UnconfiguredConfigurationProvider:
    """No UC-01 module is bound yet. Every quiz reads as unknown, so no retake is created."""

    async def get_quiz_availability(self, quiz_id: str) -> QuizAvailability | None:
        return None

    async def get_active_configuration(self, quiz_id: str) -> QuizConfigurationVersion | None:
        return None

    async def get_locked_configuration(
        self, configuration_version_id: str
    ) -> QuizConfigurationVersion | None:
        return None


class UnconfiguredQuestionBankProvider:
    """No UC-02 module is bound yet.

    Reports itself unavailable rather than returning an empty pool. The two are numerically
    identical and mean opposite things: an empty pool says "there are no alternatives, reuse is
    unavoidable", which would hand the learner the same paper and record it as expected.
    """

    async def find_eligible_questions(
        self, query: QuestionPoolQuery
    ) -> tuple[QuestionDescriptor, ...]:
        raise QuestionBankUnavailableError(
            "No question bank is bound, so the alternatives available for a retake are unknown."
        )


class UnconfiguredAttemptProvider:
    """No UC-03 module is bound yet.

    Reads report no attempts, so eligibility answers "nothing to retake". Creation refuses with a
    retryable provider error rather than inventing an attempt.
    """

    async def get_attempt(self, attempt_id: str) -> AttemptContext | None:
        return None

    async def list_attempts(self, learner_id: str, quiz_id: str) -> tuple[AttemptContext, ...]:
        return ()

    async def count_used_attempts(self, learner_id: str, course_id: str, quiz_id: str) -> int:
        return 0

    async def find_open_attempt(self, learner_id: str, quiz_id: str) -> AttemptContext | None:
        return None

    async def get_delivered_question_ids(self, attempt_id: str) -> tuple[str, ...]:
        return ()

    async def create_retake_attempt(self, request: RetakeAttemptRequest) -> DeliveredAttempt:
        raise ProviderUnavailableError(
            "No attempt delivery module is bound, so a retake attempt cannot be created."
        )


class UnconfiguredScoringProvider:
    """No UC-04 module is bound. History shows attempts with their scores labelled unavailable."""

    async def get_score(self, attempt_id: str) -> AttemptScore | None:
        return None


class UnconfiguredPassFailProvider:
    async def get_result(self, attempt_id: str) -> PassFailResult | None:
        return None


class UnconfiguredFeedbackProvider:
    async def get_feedback_availability(self, attempt_id: str) -> FeedbackAvailability | None:
        return None


class UnconfiguredCoachingProvider:
    async def get_coaching_availability(self, attempt_id: str) -> CoachingAvailability | None:
        return None


@dataclass
class Ports:
    configurations: ConfigurationProvider
    question_bank: QuestionBankProvider
    attempts: AttemptProvider
    scores: ScoringResultProvider
    results: PassFailResultProvider
    feedback: FeedbackProvider
    coaching: CoachingProvider
    audit: RetakeAuditLog


@dataclass
class Repositories:
    retakes: RetakeRequestRepository
    grants: GrantRepository


@dataclass
class Services:
    allowances: AttemptAllowanceService
    eligibility: RetakeEligibilityService
    plans: RetakeQuestionPlanService
    retakes: RetakeService
    grants: GrantService
    history: AttemptHistoryService


@dataclass
class Container:
    settings: Settings
    clock: Clock
    new_id: IdGenerator
    ports: Ports
    repositories: Repositories
    services: Services


def create_container(
    *,
    settings: Settings | None = None,
    clock: Clock | None = None,
    new_id: IdGenerator | None = None,
    configurations: ConfigurationProvider | None = None,
    question_bank: QuestionBankProvider | None = None,
    attempts: AttemptProvider | None = None,
    scores: ScoringResultProvider | None = None,
    results: PassFailResultProvider | None = None,
    feedback: FeedbackProvider | None = None,
    coaching: CoachingProvider | None = None,
    audit: RetakeAuditLog | None = None,
    retakes_repository: RetakeRequestRepository | None = None,
    grants_repository: GrantRepository | None = None,
) -> Container:
    """Build the module. Every dependency is overridable, which is how tests inject fakes."""
    config = settings or default_settings
    the_clock = clock or SystemClock()
    ids = new_id or uuid_generator

    ports = Ports(
        configurations=configurations or UnconfiguredConfigurationProvider(),
        question_bank=question_bank or UnconfiguredQuestionBankProvider(),
        attempts=attempts or UnconfiguredAttemptProvider(),
        scores=scores or UnconfiguredScoringProvider(),
        results=results or UnconfiguredPassFailProvider(),
        feedback=feedback or UnconfiguredFeedbackProvider(),
        coaching=coaching or UnconfiguredCoachingProvider(),
        audit=audit or LoggingRetakeAuditLog(),
    )

    repositories = Repositories(
        retakes=retakes_repository or InMemoryRetakeRequestRepository(),
        grants=grants_repository or InMemoryGrantRepository(),
    )

    allowances = AttemptAllowanceService(
        attempts=ports.attempts,
        grants=repositories.grants,
        retakes=repositories.retakes,
    )
    eligibility = RetakeEligibilityService(
        attempts=ports.attempts,
        configurations=ports.configurations,
        retakes=repositories.retakes,
        allowances=allowances,
        settings=config,
    )
    plans = RetakeQuestionPlanService(
        attempts=ports.attempts,
        question_bank=ports.question_bank,
    )
    retakes = RetakeService(
        attempts=ports.attempts,
        retakes=repositories.retakes,
        eligibility=eligibility,
        plans=plans,
        audit=ports.audit,
        clock=the_clock,
        new_id=ids,
        guidance=config.exhausted_contact_guidance,
    )
    grants = GrantService(
        grants=repositories.grants,
        configurations=ports.configurations,
        attempts=ports.attempts,
        retakes=repositories.retakes,
        allowances=allowances,
        audit=ports.audit,
        clock=the_clock,
        new_id=ids,
        max_additional_attempts=config.max_grant_additional_attempts,
    )
    history = AttemptHistoryService(
        attempts=ports.attempts,
        configurations=ports.configurations,
        retakes=repositories.retakes,
        scores=ports.scores,
        results=ports.results,
        feedback=ports.feedback,
        coaching=ports.coaching,
    )

    return Container(
        settings=config,
        clock=the_clock,
        new_id=ids,
        ports=ports,
        repositories=repositories,
        services=Services(
            allowances=allowances,
            eligibility=eligibility,
            plans=plans,
            retakes=retakes,
            grants=grants,
            history=history,
        ),
    )


# ---------------------------------------------------------------------------
# The merged application's wiring
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetakePorts:
    """The boundaries that need a database session, as per-session factories.

    Factories rather than instances, for the reason UC-03, the results chain and UC-07 all do the
    same: a SQLAlchemy session is not thread-safe, so each unit of work gets adapters bound to
    its own.

    ``attempts`` has no default and must be supplied. Creating a retake needs UC-03's fully wired
    service — its clock, its ports onto UC-01 and UC-02, its enrolment check — and not merely a
    database handle; but naming UC-03 here would be a cross-capability import in a composition
    root, which ``tests/test_architecture.py`` forbids and which would make this file know which
    capability is behind one of its ports. ``integration/uc03_adapter.attempt_provider_factory``
    builds the bound factory, and the application factory passes it in.
    """

    configurations: Callable[[Session], ConfigurationProvider]
    question_bank: Callable[[Session], QuestionBankProvider]
    attempts: Callable[[Session], AttemptProvider]
    scores: Callable[[Session], ScoringResultProvider]
    results: Callable[[Session], PassFailResultProvider]
    feedback: Callable[[Session], FeedbackProvider]
    coaching: Callable[[Session], CoachingProvider]
    retakes: Callable[[Session], RetakeRequestRepository]
    grants: Callable[[Session], GrantRepository]

    @classmethod
    def merged(cls, attempts: Callable[[Session], AttemptProvider]) -> RetakePorts:
        """The real adapters: UC-01, UC-02, UC-04, UC-05, UC-06, UC-07 and the ``qt_`` tables —
        plus the UC-03 provider the caller supplies.

        This one call is the whole of the integration. Substituting the company's audit pipeline
        or a different persistence store is a change to the line that names it, and nothing else.
        """
        return cls(
            configurations=RetakeConfigurationAdapter,
            question_bank=RetakeQuestionBankAdapter,
            attempts=attempts,
            scores=RetakeScoringAdapter,
            results=RetakePassFailAdapter,
            feedback=RetakeFeedbackAdapter,
            coaching=RetakeCoachingAdapter,
            retakes=SqlAlchemyRetakeRequestRepository,
            grants=SqlAlchemyGrantRepository,
        )


class RetakeAppContext:
    """Process-wide dependencies for UC-08, and the factory for one request's services.

    The counterpart of ``ResultsAppContext`` and ``CoachingAppContext``. It holds what outlives a
    request — the settings, the clock, the id generator and the audit sink — and builds a
    :class:`Container` per session on top of them.

    ``attempts`` is the UC-03 provider factory, supplied by the application factory. The one write
    UC-08 makes goes through UC-03's attempt service rather than around it — that is the
    dependency the whole module is arranged to keep narrow: one call, into the service that
    already owns attempt creation — but which capability satisfies it is not this file's business
    to know.
    """

    __slots__ = ("session_factory", "settings", "clock", "new_id", "audit", "ports")

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        attempts: Callable[[Session], AttemptProvider],
        settings: Settings | None = None,
        clock: Clock | None = None,
        new_id: IdGenerator | None = None,
        audit: RetakeAuditLog | None = None,
        ports: RetakePorts | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or default_settings
        self.clock = clock or SystemClock()
        self.new_id = new_id or uuid_generator
        # Process-wide: an audit sink is a transport, and rebuilding one per request is waste.
        self.audit = audit or LoggingRetakeAuditLog()
        self.ports = ports or RetakePorts.merged(attempts)

    def build(self, session: Session) -> Container:
        """Assemble UC-08's services for one session."""
        return create_container(
            settings=self.settings,
            clock=self.clock,
            new_id=self.new_id,
            audit=self.audit,
            configurations=self.ports.configurations(session),
            question_bank=self.ports.question_bank(session),
            attempts=self.ports.attempts(session),
            scores=self.ports.scores(session),
            results=self.ports.results(session),
            feedback=self.ports.feedback(session),
            coaching=self.ports.coaching(session),
            retakes_repository=self.ports.retakes(session),
            grants_repository=self.ports.grants(session),
        )

    @contextmanager
    def unit_of_work(self) -> Iterator[Container]:
        """A standalone unit of work, for scripts and tests.

        The repositories commit their own writes, so this only guarantees a closed session — the
        same contract the results chain and UC-07 have.
        """
        session = self.session_factory()
        try:
            yield self.build(session)
        finally:
            session.close()
