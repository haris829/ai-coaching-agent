"""UC-03, behind UC-08's ``AttemptProvider`` — the module's one write.

Five reads and one write, and the write is a single call into UC-03's own
:meth:`~app.modules.attempt_delivery.services.attempt_service.AttemptService.create_attempt`
with a :class:`~...attempt_service.RetakeDirective`. UC-08 does not build an attempt row, does
not select questions, does not compute an expiry and does not touch the question snapshot: all of
that is UC-03's, unchanged, and a retake goes through exactly the code path a first attempt does.

**Why the write goes through the service and not the repository.** Creating the row directly
would skip the enrolment check, the availability check, the one-open-attempt rule, the frozen
question snapshot and the ``record_delivery`` call UC-02's usage history depends on. Every one of
those is a rule that applies to a retake too. Reaching past the service to the table is how a
second, subtly different attempt lifecycle gets built by accident.

**The reads never mutate.** They are ``select`` statements over ``qd_`` rows and there is no
code path in this file that writes to an attempt, an answer or a snapshot — which is what makes
UC-08's promise that a retake never modifies the attempt it follows a property of the code rather
than a claim about it.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.async_db import offload
from app.core.errors import AppError
from app.core.time import iso_or_none
from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.models import AttemptQuestion, QuizAttempt
from app.modules.attempt_delivery.services.attempt_service import RetakeDirective
from app.modules.retakes.domain.errors import (
    AttemptCreationFailedError,
    AttemptDeliveryUnavailableError,
)
from app.modules.retakes.integration.uc03 import (
    AttemptContext,
    AttemptStatus,
    DeliveredAttempt,
    RetakeAttemptRequest,
)

#: Statuses that consumed an allowance. UC-03's own definition of "used" — an attempt in
#: progress has used one, which is why an abandoned attempt does not hand a learner a free retry.
_USED_STATUSES = ("ACTIVE", "SUBMISSION_PENDING", "SUBMITTED")


def _to_context(attempt: QuizAttempt) -> AttemptContext:
    return AttemptContext(
        attempt_id=attempt.id,
        learner_id=attempt.learner_id,
        course_id=attempt.course_id,
        quiz_id=attempt.quiz_id,
        attempt_number=attempt.attempt_number,
        status=AttemptStatus(attempt.status),
        configuration_version_id=attempt.configuration_version_id,
        configuration_version_number=attempt.configuration_version_number,
        started_at=iso_or_none(attempt.started_at),
        submitted_at=iso_or_none(attempt.submitted_at),
        total_questions=attempt.total_questions,
    )


class RetakeAttemptDeliveryAdapter:
    """``AttemptProvider`` over UC-03.

    Takes the module's :class:`AppContext` as well as the session because creating an attempt
    needs UC-03's fully wired service — its clock, its ports onto UC-01/UC-02 and its enrolment
    check — not just a database handle. The reads use the session directly.
    """

    __slots__ = ("_session", "_context")

    def __init__(self, session: Session, context: AppContext) -> None:
        self._session = session
        self._context = context

    # ---- reads --------------------------------------------------------------

    async def get_attempt(self, attempt_id: str) -> AttemptContext | None:
        return await offload(self._get_attempt, attempt_id)

    async def list_attempts(self, learner_id: str, quiz_id: str) -> tuple[AttemptContext, ...]:
        return await offload(self._list_attempts, learner_id, quiz_id)

    async def count_used_attempts(self, learner_id: str, course_id: str, quiz_id: str) -> int:
        return await offload(self._count_used_attempts, learner_id, quiz_id)

    async def find_open_attempt(self, learner_id: str, quiz_id: str) -> AttemptContext | None:
        return await offload(self._find_open_attempt, learner_id, quiz_id)

    async def get_delivered_question_ids(self, attempt_id: str) -> tuple[str, ...]:
        return await offload(self._get_delivered_question_ids, attempt_id)

    # ---- the one write ------------------------------------------------------

    async def create_retake_attempt(self, request: RetakeAttemptRequest) -> DeliveredAttempt:
        return await offload(self._create_retake_attempt, request)

    # ---- synchronous bodies -------------------------------------------------

    def _get_attempt(self, attempt_id: str) -> AttemptContext | None:
        try:
            attempt = self._session.scalar(
                select(QuizAttempt).where(QuizAttempt.id == attempt_id)
            )
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc
        return _to_context(attempt) if attempt else None

    def _list_attempts(self, learner_id: str, quiz_id: str) -> tuple[AttemptContext, ...]:
        try:
            rows = self._session.scalars(
                select(QuizAttempt)
                .where(QuizAttempt.learner_id == learner_id, QuizAttempt.quiz_id == quiz_id)
                .order_by(QuizAttempt.attempt_number)
            ).all()
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc
        # Every attempt, however old — history and the "what has this learner seen?" question
        # both break silently if this ever starts filtering.
        return tuple(_to_context(row) for row in rows)

    def _count_used_attempts(self, learner_id: str, quiz_id: str) -> int:
        try:
            count = self._session.scalar(
                select(func.count())
                .select_from(QuizAttempt)
                .where(
                    QuizAttempt.learner_id == learner_id,
                    QuizAttempt.quiz_id == quiz_id,
                    QuizAttempt.status.in_(_USED_STATUSES),
                )
            )
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc
        return int(count or 0)

    def _find_open_attempt(self, learner_id: str, quiz_id: str) -> AttemptContext | None:
        try:
            attempt = self._session.scalar(
                select(QuizAttempt).where(
                    QuizAttempt.learner_id == learner_id,
                    QuizAttempt.quiz_id == quiz_id,
                    QuizAttempt.status.in_(("ACTIVE", "SUBMISSION_PENDING")),
                )
            )
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc
        return _to_context(attempt) if attempt else None

    def _get_delivered_question_ids(self, attempt_id: str) -> tuple[str, ...]:
        # Ids only, in delivery order. UC-08 compares sets of identifiers and has no use for the
        # content, so the content does not cross this boundary at all.
        try:
            rows = self._session.scalars(
                select(AttemptQuestion.question_id)
                .where(AttemptQuestion.attempt_id == attempt_id)
                .order_by(AttemptQuestion.position)
            ).all()
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc
        return tuple(rows)

    def _create_retake_attempt(self, request: RetakeAttemptRequest) -> DeliveredAttempt:
        """Ask UC-03 to deliver the retake. All-or-nothing, because UC-03's creation is.

        UC-03 commits the attempt row, its whole question set and UC-02's delivery record in one
        transaction and rolls the lot back on failure, so there is no partially created attempt
        for UC-08's reservation to disagree with.
        """
        services = self._context.build(self._session)
        try:
            result = services.attempts.create_attempt(
                request.learner_id,
                request.quiz_id,
                retake=RetakeDirective(
                    previous_attempt_id=request.retake_of_attempt_id,
                    configuration_version_id=request.configuration_version_id,
                    attempt_number=request.attempt_number,
                    deprioritised_question_ids=tuple(request.deprioritised_question_ids),
                ),
            )
        except AppError as exc:
            # UC-03 refused by its own rules — no enrolment, quiz withdrawn, an attempt already
            # open, not enough eligible questions. Re-raised in UC-08's taxonomy so a caller sees
            # one error vocabulary, with UC-03's code preserved as context rather than discarded.
            raise AttemptCreationFailedError(
                exc.message,
                upstream_code=exc.code,
                learner_id=request.learner_id,
                quiz_id=request.quiz_id,
                attempt_number=request.attempt_number,
            ) from exc
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc

        attempt = result.attempt
        return DeliveredAttempt(
            attempt_id=attempt.id,
            learner_id=attempt.learner_id,
            course_id=attempt.course_id,
            quiz_id=attempt.quiz_id,
            attempt_number=attempt.attempt_number,
            status=AttemptStatus(attempt.status),
            configuration_version_id=attempt.configuration_version_id,
            configuration_version_number=attempt.configuration_version_number,
            delivered_question_ids=tuple(
                question.question_id
                for question in sorted(result.questions, key=lambda item: item.position)
            ),
            started_at=iso_or_none(attempt.started_at),
            delivery_mode=attempt.question_presentation,
            time_limit_seconds=attempt.time_limit_seconds,
        )


def attempt_provider_factory(
    delivery: AppContext,
) -> Callable[[Session], RetakeAttemptDeliveryAdapter]:
    """A per-session ``AttemptProvider`` factory bound to UC-03's composition root.

    Exists so that UC-08's ``container.py`` never has to name UC-03. The composition root binds
    ports; it is not allowed to know *which* capability is behind one, and
    ``tests/test_architecture.py`` enforces that a cross-capability import lives only in an
    ``integration/`` package — this file. The application factory calls this once at start-up and
    hands the result to :class:`RetakeAppContext`, which sees only ``Callable[[Session], ...]``.
    """
    return lambda session: RetakeAttemptDeliveryAdapter(session, delivery)
