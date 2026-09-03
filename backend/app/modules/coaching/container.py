"""Composition root (§31, §32, §35 phase 14).

Wiring lives here and nowhere else, which is what keeps every boundary swappable: the services
depend on the protocols in ``integration`` and ``repositories``, and the decision about which
implementation satisfies them is made at this single point.

UC-07 was built standalone, and this file was written as the one place a merge would touch. It was:
every port below is now bound to something real, and no domain rule, no service and no test of the
coaching logic changed to make that happen.

============================  ==========================================================
Port                          Bound in the merged application to
============================  ==========================================================
``attempts``                  UC-03 — ``integration/uc03_adapter.py``
``scores``                    UC-04 — ``integration/uc04_adapter.py``
``feedback``                  UC-06 — ``integration/uc06_adapter.py``
``sessions``/``transcripts``  the ``qk_`` tables — ``repositories/sqlalchemy.py``
``activity``                  ``qk_coaching_activity``, behind the same port
``knowledge_gaps``            ``qk_knowledge_gaps``, behind the same port
``llm``                       the AI provider named in configuration, or nothing
                              (``anthropic`` or ``bedrock`` — see ``integration/llm_factory``)
============================  ==========================================================

:class:`CoachingPorts.merged` is that table in code, and the two outbound streams are still ports
precisely so the company's activity pipeline and knowledge-gap store can replace them by changing
the line that names them.

THE UNCONFIGURED DEFAULTS
-------------------------
:func:`create_container` still defaults every port to an implementation that returns nothing rather
than a stub that returns plausible data — that is what the coaching tests run against, and what a
standalone deployment of this module would run on. The distinction is the whole of §6: a fake
attempt provider would let coaching run against an attempt that was never submitted, and a fake LLM
would put invented teaching in front of a learner. Returning ``None`` — and, for the model,
reporting itself unavailable — makes an unwired deployment say "coaching is not available", which is
the truth.

That default is not hypothetical in the merged application either: no AI provider is bound unless
``COACHING_LLM_PROVIDER`` and an API key are configured, so a stock deployment honestly reports
coaching as unavailable while the rest of the quiz chain works exactly as before.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.time import Clock, SystemClock
from app.modules.coaching.domain.sanitizer import CoachingContextSanitizer
from app.modules.coaching.ids import IdGenerator, uuid_generator
from app.modules.coaching.integration.activity import (
    CoachingActivityLog,
    LoggingCoachingActivityLog,
)
from app.modules.coaching.integration.knowledge_gaps import (
    KnowledgeGapTracker,
    LoggingKnowledgeGapTracker,
)
from app.modules.coaching.integration.llm import CoachingLLM, UnconfiguredCoachingLLM
from app.modules.coaching.integration.llm_factory import build_coaching_llm
from app.modules.coaching.integration.uc03 import (
    AttemptContext,
    AttemptProvider,
    DeliveredQuestion,
    LearnerAnswer,
)
from app.modules.coaching.integration.uc03_adapter import AttemptDeliveryCoachingAdapter
from app.modules.coaching.integration.uc04 import AttemptScore, ScoringResultProvider
from app.modules.coaching.integration.uc04_adapter import ScoringCoachingAdapter
from app.modules.coaching.integration.uc06 import AttemptFeedback, FeedbackProvider
from app.modules.coaching.integration.uc06_adapter import FeedbackCoachingAdapter
from app.modules.coaching.integration.uc09 import (
    FormalAssessmentPolicyPort,
    UnrestrictedFormalAssessmentPolicy,
)
from app.modules.coaching.integration.uc09_adapter import FormalAssessmentCoachingAdapter
from app.modules.coaching.repositories.in_memory import (
    InMemoryCoachingSessionRepository,
    InMemoryCoachingTranscriptRepository,
)
from app.modules.coaching.repositories.protocols import (
    CoachingSessionRepository,
    CoachingTranscriptRepository,
)
from app.modules.coaching.repositories.sqlalchemy import (
    SqlAlchemyCoachingActivityLog,
    SqlAlchemyCoachingSessionRepository,
    SqlAlchemyCoachingTranscriptRepository,
    SqlAlchemyKnowledgeGapTracker,
)
from app.modules.coaching.services.authorization import CoachingAuthorizer
from app.modules.coaching.services.coaching_service import CoachingService
from app.modules.coaching.services.context_builder import CoachingContextBuilder
from app.modules.coaching.services.review_service import CoachingReviewService


class UnconfiguredAttemptProvider:
    """No UC-03 module is bound yet.

    Returns no attempt, which the gate reads as ATTEMPT_NOT_FOUND. An unwired deployment refuses
    coaching rather than coaching a learner about an attempt nobody can see (§6).
    """

    async def get_attempt(self, attempt_id: str) -> AttemptContext | None:
        return None

    async def get_delivered_questions(self, attempt_id: str) -> tuple[DeliveredQuestion, ...]:
        return ()

    async def get_learner_answers(self, attempt_id: str) -> tuple[LearnerAnswer, ...]:
        return ()


class UnconfiguredScoringProvider:
    """No UC-04 module is bound yet.

    Returns ``None`` rather than inventing outcomes: without an authoritative result there is no
    honest way to say which questions were answered incorrectly, and guessing would put a learner
    in a coaching conversation about a question they got right (§20).
    """

    async def get_score(self, attempt_id: str) -> AttemptScore | None:
        return None


class UnconfiguredFeedbackProvider:
    """No UC-06 module is bound yet. Coaching therefore stays behind the feedback gate (§7)."""

    async def get_attempt_feedback(self, attempt_id: str) -> AttemptFeedback | None:
        return None


@dataclass
class Ports:
    attempts: AttemptProvider
    scores: ScoringResultProvider
    feedback: FeedbackProvider
    llm: CoachingLLM
    activity: CoachingActivityLog
    knowledge_gaps: KnowledgeGapTracker
    #: UC-09. Whether a formal assessment of this learner's is in progress (§7).
    formal_assessment: FormalAssessmentPolicyPort


@dataclass
class Repositories:
    sessions: CoachingSessionRepository
    transcripts: CoachingTranscriptRepository


@dataclass
class Services:
    authorizer: CoachingAuthorizer
    context: CoachingContextBuilder
    coaching: CoachingService
    review: CoachingReviewService


@dataclass
class Container:
    settings: Settings
    clock: Clock
    new_id: IdGenerator
    sanitizer: CoachingContextSanitizer
    ports: Ports
    repositories: Repositories
    services: Services


def create_container(
    *,
    settings: Settings | None = None,
    clock: Clock | None = None,
    new_id: IdGenerator | None = None,
    attempts: AttemptProvider | None = None,
    scores: ScoringResultProvider | None = None,
    feedback: FeedbackProvider | None = None,
    llm: CoachingLLM | None = None,
    activity: CoachingActivityLog | None = None,
    knowledge_gaps: KnowledgeGapTracker | None = None,
    formal_assessment: FormalAssessmentPolicyPort | None = None,
    sessions_repository: CoachingSessionRepository | None = None,
    transcripts_repository: CoachingTranscriptRepository | None = None,
    sanitizer: CoachingContextSanitizer | None = None,
) -> Container:
    """Build the module. Every dependency is overridable, which is how tests inject fakes."""
    config = settings or default_settings
    the_clock = clock or SystemClock()
    ids = new_id or uuid_generator

    ports = Ports(
        attempts=attempts or UnconfiguredAttemptProvider(),
        scores=scores or UnconfiguredScoringProvider(),
        feedback=feedback or UnconfiguredFeedbackProvider(),
        # Reports itself unavailable rather than answering — see the module docstring.
        llm=llm or UnconfiguredCoachingLLM(),
        activity=activity or LoggingCoachingActivityLog(),
        knowledge_gaps=knowledge_gaps or LoggingKnowledgeGapTracker(),
        # Allowing is the honest default here, unlike every other port in this container: without
        # UC-09 there are no formal assessments, so there is nothing to be in the middle of. See
        # ``integration/uc09.py`` — an *unreadable* UC-09 is a different case and raises.
        formal_assessment=formal_assessment or UnrestrictedFormalAssessmentPolicy(),
    )

    repositories = Repositories(
        sessions=sessions_repository or InMemoryCoachingSessionRepository(),
        transcripts=transcripts_repository or InMemoryCoachingTranscriptRepository(),
    )

    # One sanitiser instance, shared. It is stateless; a per-request one would only make it look
    # like there were several answer-key policies in play.
    the_sanitizer = sanitizer or CoachingContextSanitizer()

    authorizer = CoachingAuthorizer(
        attempts=ports.attempts,
        scores=ports.scores,
        feedback=ports.feedback,
        llm=ports.llm,
        formal_assessment=ports.formal_assessment,
    )
    context_builder = CoachingContextBuilder(
        attempts=ports.attempts, sanitizer=the_sanitizer
    )
    coaching = CoachingService(
        authorizer=authorizer,
        context_builder=context_builder,
        sessions=repositories.sessions,
        transcripts=repositories.transcripts,
        llm=ports.llm,
        activity=ports.activity,
        knowledge_gaps=ports.knowledge_gaps,
        clock=the_clock,
        new_id=ids,
        settings=config,
    )
    review = CoachingReviewService(
        authorizer=authorizer,
        attempts=ports.attempts,
        sessions=repositories.sessions,
        coaching=coaching,
    )

    return Container(
        settings=config,
        clock=the_clock,
        new_id=ids,
        sanitizer=the_sanitizer,
        ports=ports,
        repositories=repositories,
        services=Services(
            authorizer=authorizer,
            context=context_builder,
            coaching=coaching,
            review=review,
        ),
    )


# ---------------------------------------------------------------------------
# The merged application's wiring
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoachingPorts:
    """The boundaries that need a database session, as per-session factories.

    Factories rather than instances, for the reason UC-03 and the results chain do the same: a
    SQLAlchemy session is not thread-safe, so each unit of work gets adapters bound to its own.

    The AI provider, the clock and the id generator take no session and live on
    :class:`CoachingAppContext` instead — they are process-wide, and rebuilding an HTTP client per
    request would be waste.
    """

    attempts: Callable[[Session], AttemptProvider]
    scores: Callable[[Session], ScoringResultProvider]
    feedback: Callable[[Session], FeedbackProvider]
    sessions: Callable[[Session], CoachingSessionRepository]
    transcripts: Callable[[Session], CoachingTranscriptRepository]
    activity: Callable[[Session], CoachingActivityLog]
    knowledge_gaps: Callable[[Session], KnowledgeGapTracker]
    #: UC-09. Whether a formal assessment of this learner's is in progress — asked on every
    #: coaching operation. Learner-scoped, not attempt-scoped; see ``integration/uc09.py``.
    formal_assessment: Callable[[Session], FormalAssessmentPolicyPort]

    @classmethod
    def merged(cls) -> CoachingPorts:
        """The real adapters: UC-03, UC-04, UC-06 and UC-07's own ``qk_`` tables.

        This one call is the whole of the integration. Substituting the company's activity pipeline
        or knowledge-gap store is a change to the line that names it, and nothing else — no domain
        rule, no service and no test of the coaching logic moves with it.
        """
        return cls(
            attempts=AttemptDeliveryCoachingAdapter,
            scores=ScoringCoachingAdapter,
            feedback=FeedbackCoachingAdapter,
            sessions=SqlAlchemyCoachingSessionRepository,
            transcripts=SqlAlchemyCoachingTranscriptRepository,
            activity=SqlAlchemyCoachingActivityLog,
            knowledge_gaps=SqlAlchemyKnowledgeGapTracker,
            formal_assessment=FormalAssessmentCoachingAdapter,
        )


class CoachingAppContext:
    """Process-wide dependencies for UC-07, and the factory for one request's services.

    The counterpart of ``app.composition.ResultsAppContext``. It holds what outlives a request — the
    settings, the clock, the id generator, the stateless sanitiser and the bound AI provider — and
    builds a :class:`Container` per session on top of them.

    The AI provider is built **once**, at start-up, and shared. That is not only an efficiency
    argument: ``AnthropicCoachingLLM`` remembers consecutive failures so an outage degrades to
    "coaching unavailable" quickly, and a per-request adapter would forget every time.
    """

    __slots__ = ("session_factory", "settings", "clock", "new_id", "sanitizer", "llm", "ports")

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings | None = None,
        clock: Clock | None = None,
        new_id: IdGenerator | None = None,
        llm: CoachingLLM | None = None,
        ports: CoachingPorts | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or default_settings
        self.clock = clock or SystemClock()
        self.new_id = new_id or uuid_generator
        # Stateless and shared. A per-request sanitiser would only make it look as though there were
        # several answer-key policies in play.
        self.sanitizer = CoachingContextSanitizer()
        # No provider configured -> the honest default, which reports coaching unavailable rather
        # than inventing teaching text.
        self.llm = llm or build_coaching_llm(self.settings, clock=self.clock) or (
            UnconfiguredCoachingLLM()
        )
        self.ports = ports or CoachingPorts.merged()

    def build(self, session: Session) -> Container:
        """Assemble UC-07's services for one session."""
        return create_container(
            settings=self.settings,
            clock=self.clock,
            new_id=self.new_id,
            sanitizer=self.sanitizer,
            llm=self.llm,
            attempts=self.ports.attempts(session),
            scores=self.ports.scores(session),
            feedback=self.ports.feedback(session),
            sessions_repository=self.ports.sessions(session),
            transcripts_repository=self.ports.transcripts(session),
            activity=self.ports.activity(session),
            knowledge_gaps=self.ports.knowledge_gaps(session),
            formal_assessment=self.ports.formal_assessment(session),
        )

    @contextmanager
    def unit_of_work(self) -> Iterator[Container]:
        """A standalone unit of work, for scripts and tests.

        The repositories commit their own writes, so this only guarantees a closed session — the
        same contract the results chain's unit of work has.
        """
        session = self.session_factory()
        try:
            yield self.build(session)
        finally:
            session.close()
