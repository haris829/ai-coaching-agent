"""The application composition root for the result chain: UC-04 -> UC-05 -> UC-06.

UC-03 has a composition root of its own (``attempt_delivery/container.py``) because it owns
attempts. This is the equivalent for the three capabilities that run *after* a submission, and it
lives at application level rather than inside any of them for one reason: assembling the chain
means naming all three, and a capability that named the other two would no longer be a capability.

What it provides
----------------
:class:`ResultsPorts`        the seven inbound and two outbound boundaries, as per-session factories
:class:`ResultsContext`      the three services, bound to one session
:class:`ResultsAppContext`   process-wide: session factory, clock and ports
:class:`ResultsPipeline`     UC-03's ``SubmissionDispatchPort``, as score -> gate -> feedback

The pipeline is the seam UC-03 always had. Its port documented the downstream grading capability
as "a future use case" and shipped a no-op default; wiring this in its place is the whole of the
integration, and UC-03's submission service is unchanged.

Failure isolation
-----------------
The pipeline runs **after** UC-03 has committed the attempt and frozen its answers, in its own
unit of work, and every stage is guarded:

* scoring cannot fail the submission -- a failure leaves the result ``PENDING_SCORE``;
* pass/fail cannot run without a confirmed score, and says so rather than guessing;
* certificate, CPD and feedback failures leave their own rows pending and retryable.

So the worst case for a learner is "submitted, score pending, retry available", never a lost
submission. Each stage is separately retryable through its own endpoint."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.core.time import Clock, SystemClock
from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.integration.submission_dispatch.port import (
    SubmissionDispatchPort,
    SubmissionDispatchRequest,
    SubmissionDispatchResult,
)
from app.modules.certification.integration.attempt_delivery.attempt_policy_adapter import (
    AttemptPolicyAdapter,
)
from app.modules.certification.integration.attempt_delivery.port import AttemptPolicyPort
from app.modules.certification.integration.certificate.local_adapter import (
    LocalCertificateService,
)
from app.modules.certification.integration.certificate.port import CertificateServicePort
from app.modules.certification.integration.cpd.local_adapter import LocalCpdSyncService
from app.modules.certification.integration.cpd.port import CpdSyncPort
from app.modules.certification.integration.formal_gate import (
    CertificateGatePort,
    UnrestrictedCertificateGate,
)
from app.modules.certification.integration.formal_gate_adapter import (
    FormalCertificateGateAdapter,
)
from app.modules.certification.integration.scoring.port import ScoreResultPort
from app.modules.certification.integration.scoring.result_adapter import ScoringResultAdapter
from app.modules.certification.repositories import SqlAlchemyCertificationRepository
from app.modules.certification.services.certification_service import CertificationService
from app.modules.feedback.integration.certification.outcome_adapter import (
    CertificationOutcomeAdapter,
)
from app.modules.feedback.integration.certification.port import OutcomePort
from app.modules.feedback.integration.question_bank.content_adapter import (
    QuestionContentAdapter,
)
from app.modules.feedback.integration.question_bank.port import QuestionContentPort
from app.modules.feedback.integration.scoring.port import ScoreDetailPort
from app.modules.feedback.integration.scoring.score_adapter import ScoringDetailAdapter
from app.modules.feedback.repositories import SqlAlchemyFeedbackRepository
from app.modules.feedback.services.feedback_service import FeedbackService
from app.modules.scoring.integration.attempt_delivery.attempt_adapter import (
    AttemptDeliveryAdapter,
)
from app.modules.scoring.integration.attempt_delivery.port import AttemptSourcePort
from app.modules.scoring.integration.question_bank.answer_key_adapter import (
    QuestionBankAnswerKeyAdapter,
)
from app.modules.scoring.integration.question_bank.port import AnswerKeyPort
from app.modules.scoring.repositories import SqlAlchemyResultRepository
from app.modules.scoring.services.scoring_service import ScoringService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ResultsPorts:
    """Every boundary the result chain depends on, as per-session factories.

    Session factories rather than instances, for the same reason UC-03 does it: a session is not
    thread-safe, so each unit of work gets adapters bound to its own. The two outbound services take
    no session at all -- they are remote calls, and today's local implementations hold no state.
    """

    # UC-04 -> UC-03, UC-02
    attempts: Callable[[Session], AttemptSourcePort]
    answer_keys: Callable[[Session], AnswerKeyPort]
    # UC-05 -> UC-04, UC-03, and the two outbound services
    scores: Callable[[Session], ScoreResultPort]
    policies: Callable[[Session], AttemptPolicyPort]
    certificates: CertificateServicePort
    cpd: CpdSyncPort
    # UC-06 -> UC-04, UC-05, UC-02
    score_details: Callable[[Session], ScoreDetailPort]
    outcomes: Callable[[Session], OutcomePort]
    content: Callable[[Session], QuestionContentPort]
    #: UC-05 -> UC-09. The condition that withholds a certificate for a formal assessment nobody
    #: has approved yet.
    #:
    #: Last, and defaulted, so every caller that built these ports before UC-09 existed still
    #: does — and so a test that wants the results chain without a formal gate gets the honest
    #: answer rather than having to construct one. ``UnrestrictedCertificateGate`` reports "not a
    #: formal assessment", which is the truth wherever UC-09 is not bound.
    formal_gate: Callable[[Session], CertificateGatePort] = (
        lambda _session: UnrestrictedCertificateGate()
    )

    @classmethod
    def merged(
        cls,
        *,
        certificates: CertificateServicePort | None = None,
        cpd: CpdSyncPort | None = None,
    ) -> ResultsPorts:
        """The real adapters, with the local certificate and CPD services.

        Pointing either outbound port at the company's service is a change to this one call.
        """
        return cls(
            attempts=AttemptDeliveryAdapter,
            answer_keys=QuestionBankAnswerKeyAdapter,
            scores=ScoringResultAdapter,
            policies=AttemptPolicyAdapter,
            certificates=certificates or LocalCertificateService(),
            cpd=cpd or LocalCpdSyncService(),
            formal_gate=FormalCertificateGateAdapter,
            score_details=ScoringDetailAdapter,
            outcomes=CertificationOutcomeAdapter,
            content=QuestionContentAdapter,
        )


@dataclass(slots=True)
class ResultsContext:
    """The three services, bound to one session."""

    session: Session
    scoring: ScoringService
    certification: CertificationService
    feedback: FeedbackService


class ResultsAppContext:
    """Process-wide dependencies for UC-04, UC-05 and UC-06."""

    __slots__ = ("session_factory", "clock", "ports")

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        clock: Clock | None = None,
        ports: ResultsPorts | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock or SystemClock()
        self.ports = ports or ResultsPorts.merged()

    @classmethod
    def from_attempt_context(
        cls, attempt_context: AppContext, *, ports: ResultsPorts | None = None
    ) -> ResultsAppContext:
        """Share UC-03's session factory and clock.

        Not a convenience: it is what keeps the chain on one database and one clock. UC-03's tests
        give their context a private in-memory engine and a fixed clock, and the result chain has to
        be on the same engine to see the attempt at all -- and on the same clock for its timestamps
        to line up with the attempt's.
        """
        return cls(
            session_factory=attempt_context.session_factory,
            clock=attempt_context.clock,
            ports=ports,
        )

    def build(self, session: Session) -> ResultsContext:
        """Assemble the three services for one session."""
        scoring = ScoringService(
            session=session,
            results=SqlAlchemyResultRepository(session),
            attempts=self.ports.attempts(session),
            answer_keys=self.ports.answer_keys(session),
            clock=self.clock,
        )
        certification = CertificationService(
            session=session,
            repository=SqlAlchemyCertificationRepository(session),
            results=self.ports.scores(session),
            attempts=self.ports.policies(session),
            certificates=self.ports.certificates,
            cpd=self.ports.cpd,
            clock=self.clock,
            formal_gate=self.ports.formal_gate(session),
        )
        feedback = FeedbackService(
            session=session,
            reports=SqlAlchemyFeedbackRepository(session),
            scores=self.ports.score_details(session),
            outcomes=self.ports.outcomes(session),
            content=self.ports.content(session),
            clock=self.clock,
        )
        return ResultsContext(
            session=session, scoring=scoring, certification=certification, feedback=feedback
        )

    @contextmanager
    def unit_of_work(self) -> Iterator[ResultsContext]:
        """A standalone unit of work, for the pipeline, scripts and tests."""
        session = self.session_factory()
        try:
            yield self.build(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class ResultsPipeline:
    """UC-03's downstream hand-off, implemented as the result chain.

    Substituted for :class:`NoopSubmissionDispatch` at application start-up. ``inner`` is called
    first when supplied, so a deployment that also has a real downstream consumer keeps it. Nothing
    here raises. A submission has already been committed by the time this runs, and the whole design
    of the chain is that a downstream problem becomes a retryable pending state rather than a failed
    submission. Exceptions are logged with the attempt id and swallowed; the retry endpoints and the
    per-row statuses are how the work is picked back up.
    """

    __slots__ = ("_context", "_inner")

    def __init__(
        self, context: ResultsAppContext, *, inner: SubmissionDispatchPort | None = None
    ) -> None:
        self._context = context
        self._inner = inner

    def dispatch(self, request: SubmissionDispatchRequest) -> SubmissionDispatchResult:
        inner_result = (
            self._inner.dispatch(request) if self._inner is not None else SubmissionDispatchResult()
        )

        stages: dict[str, str] = {}
        result_id: str | None = None

        with self._context.unit_of_work() as ctx:
            # ---- UC-04: score -------------------------------------------------
            try:
                scored = ctx.scoring.score(request.attempt_id)
                result_id = scored.result.id
                stages["score"] = scored.result.status
            except Exception as exc:
                # noqa: BLE001 - a submission is never failed by scoring
                stages["score"] = "ERROR"
                logger.error(
                    "pipeline.scoring_failed",
                    extra={"attemptId": request.attempt_id},
                    exc_info=exc,
                )
                return _dispatch_result(inner_result, result_id, stages)

            if not scored.confirmed:
                # "Submitted -- Pending Score". Gating and feedback both need a confirmed score, so
                # the chain stops here rather than producing a verdict nobody can stand behind.
                stages["outcome"] = "SKIPPED_PENDING_SCORE"
                stages["feedback"] = "SKIPPED_PENDING_SCORE"
                return _dispatch_result(inner_result, result_id, stages)

            # ---- UC-05: pass/fail, certificate, CPD ---------------------------
            try:
                outcome = ctx.certification.determine(request.attempt_id)
                stages["outcome"] = outcome.outcome.outcome
                stages["certificate"] = (
                    outcome.certificate.status if outcome.certificate is not None else "NOT_DUE"
                )
                stages["cpd"] = (
                    outcome.cpd_record.status if outcome.cpd_record is not None else "NONE"
                )
            except Exception as exc:
                # noqa: BLE001 - the score survives a gating failure
                stages["outcome"] = "ERROR"
                logger.error(
                    "pipeline.outcome_failed",
                    extra={"attemptId": request.attempt_id},
                    exc_info=exc,
                )

            # ---- UC-06: feedback ---------------------------------------------
            try:
                feedback = ctx.feedback.generate(request.attempt_id, raise_on_failure=False)
                stages["feedback"] = feedback.report.status
            except Exception as exc:
                # noqa: BLE001 - the score and the verdict survive this too
                stages["feedback"] = "ERROR"
                logger.error(
                    "pipeline.feedback_failed",
                    extra={"attemptId": request.attempt_id},
                    exc_info=exc,
                )

        return _dispatch_result(inner_result, result_id, stages)


def _dispatch_result(
    inner: SubmissionDispatchResult, result_id: str | None, stages: dict[str, str]
) -> SubmissionDispatchResult:
    """Report what the chain did back to UC-03, without changing its submission semantics.

    ``downstream_reference`` keeps whatever an inner dispatcher returned, falling back to the result
    id, so the submission response points at something meaningful either way.
    """
    return SubmissionDispatchResult(
        downstream_reference=inner.downstream_reference or result_id,
        metadata={**inner.metadata, "resultId": result_id, "stages": stages},
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def get_results_app_context(request: Request) -> ResultsAppContext:
    context = getattr(request.app.state, "results", None)
    if context is None:
        # pragma: no cover - application wiring error
        raise RuntimeError("The application was created without a ResultsAppContext.")
    return context


def get_results_context(request: Request) -> Iterator[ResultsContext]:
    """A session-scoped :class:`ResultsContext` for one request.

    The services commit their own units of work, so this only guarantees rollback on an unhandled
    error and a closed session -- the same contract UC-03's request context has.
    """
    app_context = get_results_app_context(request)
    session = app_context.session_factory()
    try:
        yield app_context.build(session)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


ResultsCtx = Annotated[ResultsContext, Depends(get_results_context)]
