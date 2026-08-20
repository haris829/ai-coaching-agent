"""UC-03, behind UC-09's ``AttemptProvider``.

UC-09 supervises a sitting; UC-03 delivers it. Every call here goes through UC-03's own services
rather than around them, so a formal attempt is an ordinary attempt with an extra lifecycle beside
it — not a second quiz engine.

    create_attempt              -> AttemptService.create_attempt(formal_assessment=True)
    save_answers                -> AnswerService.save_many        (the autosave UC-03 already has)
    get_latest_autosaved_state  -> AnswerService.list_answers
    submit_attempt              -> SubmissionService.confirm / submit_on_disconnect
    get_attempt_responses       -> UC-03's answers + UC-04's per-question outcomes

**UC-09 adds no timer, no autosave, no answer validation and no scoring.** It has no code that
could: the only thing it contributes to delivery is the ``formal_assessment=True`` flag, and the
only thing it contributes to submission is *which reason applies*.

**Submission is idempotent on the attempt, not on a token UC-09 invents.** UC-03's submission
record is already unique per attempt, so several disconnect events — or a learner confirming while
a monitor reports a disconnect — converge on one submission. ``already_submitted`` is a
return-value mapping of that existing guarantee, not new behaviour.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.async_db import offload
from app.core.errors import AppError
from app.core.time import iso_or_none
from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.models import AttemptAnswer, AttemptQuestion, QuizAttempt
from app.modules.formal_assessment.domain.enums import FormalSubmissionReason
from app.modules.formal_assessment.domain.errors import (
    AttemptDeliveryUnavailableError,
    FormalAttemptCreationFailedError,
)
from app.modules.formal_assessment.integration.uc03 import (
    AttemptContext,
    AutosavedState,
    CreateAttemptRequest,
    QuestionResponse,
    SubmissionRequest,
    SubmittedState,
)
from app.modules.scoring.models import QuestionScoreRow


def _to_context(attempt: QuizAttempt, *, answered: int | None = None) -> AttemptContext:
    return AttemptContext(
        attempt_id=attempt.id,
        learner_id=attempt.learner_id,
        course_id=attempt.course_id,
        quiz_id=attempt.quiz_id,
        status=attempt.status,
        attempt_number=attempt.attempt_number,
        configuration_version_id=attempt.configuration_version_id,
        started_at=iso_or_none(attempt.started_at),
        submitted_at=iso_or_none(attempt.submitted_at),
        # UC-03's server-authoritative expiry, carried through untouched. UC-09 never re-measures
        # it: two clocks deciding when an exam ends is one clock too many.
        expires_at=iso_or_none(attempt.expires_at),
        total_questions=attempt.total_questions,
        answered_questions=answered,
        submission_reason=attempt.submission_reason,
    )


class FormalAttemptDeliveryAdapter:
    """``AttemptProvider`` over UC-03."""

    __slots__ = ("_session", "_context")

    def __init__(self, session: Session, context: AppContext) -> None:
        self._session = session
        self._context = context

    # ---- reads --------------------------------------------------------------

    async def get_attempt(self, attempt_id: str) -> AttemptContext | None:
        return await offload(self._get_attempt, attempt_id)

    async def find_open_attempt(self, learner_id: str, quiz_id: str) -> AttemptContext | None:
        return await offload(self._find_open_attempt, learner_id, quiz_id)

    async def get_latest_autosaved_state(self, attempt_id: str) -> AutosavedState | None:
        return await offload(self._get_latest_autosaved_state, attempt_id)

    async def get_attempt_responses(self, attempt_id: str) -> tuple[QuestionResponse, ...]:
        return await offload(self._get_attempt_responses, attempt_id)

    # ---- writes, all through UC-03's services -------------------------------

    async def create_attempt(self, request: CreateAttemptRequest) -> AttemptContext:
        return await offload(self._create_attempt, request)

    async def save_answers(self, attempt_id: str, answers: Any) -> AutosavedState:
        return await offload(self._save_answers, attempt_id, answers)

    async def submit_attempt(self, request: SubmissionRequest) -> SubmittedState:
        return await offload(self._submit_attempt, request)

    # ---- synchronous bodies -------------------------------------------------

    def _row(self, attempt_id: str) -> QuizAttempt | None:
        try:
            return self._session.scalar(select(QuizAttempt).where(QuizAttempt.id == attempt_id))
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc

    def _answered_count(self, attempt_id: str) -> int:
        try:
            rows = self._session.scalars(
                select(AttemptAnswer.answered).where(AttemptAnswer.attempt_id == attempt_id)
            ).all()
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc
        return sum(1 for answered in rows if answered)

    def _get_attempt(self, attempt_id: str) -> AttemptContext | None:
        attempt = self._row(attempt_id)
        if attempt is None:
            return None
        return _to_context(attempt, answered=self._answered_count(attempt_id))

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
        if attempt is None:
            return None
        return _to_context(attempt, answered=self._answered_count(attempt.id))

    def _get_latest_autosaved_state(self, attempt_id: str) -> AutosavedState | None:
        """What UC-03 has saved so far — the state a disconnect commits.

        Read from UC-03's answer rows, which are the same rows a submission reads. UC-09 must not
        keep its own copy of a learner's answers: two records of what was typed would eventually
        disagree, and the one an assessor saw would be the wrong one.
        """
        attempt = self._row(attempt_id)
        if attempt is None:
            return None
        try:
            rows = self._session.execute(
                select(AttemptAnswer.question_id, AttemptAnswer.answered, AttemptAnswer.saved_at)
                .where(AttemptAnswer.attempt_id == attempt_id)
                .order_by(AttemptAnswer.saved_at)
            ).all()
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc

        answered_ids = tuple(row.question_id for row in rows if row.answered)
        saved_at = max((row.saved_at for row in rows if row.saved_at), default=None)
        return AutosavedState(
            attempt_id=attempt_id,
            saved_at=iso_or_none(saved_at),
            answered_questions=len(answered_ids),
            total_questions=attempt.total_questions,
            answered_question_ids=answered_ids,
            exists=bool(rows),
        )

    def _get_attempt_responses(self, attempt_id: str) -> tuple[QuestionResponse, ...]:
        """The paper as an assessor reviews it: what was asked, what was answered, how it scored.

        Assembled from UC-03's delivered questions and answers plus UC-04's per-question outcomes.
        Correctness comes from UC-04 and is not recomputed — an assessor must see the marks the
        system actually awarded, not a second opinion about them.
        """
        try:
            questions = self._session.scalars(
                select(AttemptQuestion)
                .where(AttemptQuestion.attempt_id == attempt_id)
                .order_by(AttemptQuestion.position)
            ).all()
            answers = {
                row.question_id: row
                for row in self._session.scalars(
                    select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt_id)
                ).all()
            }
            scores = {
                row.question_id: row
                for row in self._session.scalars(
                    select(QuestionScoreRow).where(QuestionScoreRow.attempt_id == attempt_id)
                ).all()
            }
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc

        responses: list[QuestionResponse] = []
        for question in questions:
            answer = answers.get(question.question_id)
            score = scores.get(question.question_id)
            snapshot: dict[str, Any] = question.question_snapshot or {}
            responses.append(
                QuestionResponse(
                    question_id=question.question_id,
                    position=question.position,
                    question_type=question.question_type,
                    prompt=snapshot.get("prompt"),
                    answered=bool(answer.answered) if answer else False,
                    response=answer.response if answer else None,
                    correct=(score.outcome == "CORRECT") if score else None,
                    marks_awarded=score.awarded_marks if score else None,
                    marks_available=score.maximum_marks if score else question.points,
                )
            )
        return tuple(responses)

    def _create_attempt(self, request: CreateAttemptRequest) -> AttemptContext:
        """Ask UC-03 to deliver the attempt, marked as a formal sitting."""
        services = self._context.build(self._session)
        try:
            result = services.attempts.create_attempt(
                request.learner_id,
                request.quiz_id,
                formal_assessment=request.formal_assessment,
            )
        except AppError as exc:
            # UC-03 refused by its own rules — not enrolled, quiz withdrawn, an attempt already
            # open, no attempts remaining. Re-raised in UC-09's taxonomy with UC-03's code kept as
            # context, so the learner is told why rather than being shown a generic failure.
            raise FormalAttemptCreationFailedError(
                exc.message,
                upstream_code=exc.code,
                learner_id=request.learner_id,
                quiz_id=request.quiz_id,
            ) from exc
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc

        attempt = result.attempt
        return _to_context(attempt, answered=0)

    def _save_answers(self, attempt_id: str, answers: Any) -> AutosavedState:
        """Autosave through UC-03's own answer service.

        UC-09 does not validate an answer, hash it, version it or decide whether it is complete.
        All of that is UC-03's, and a supervised sitting gets exactly the same treatment as an
        ordinary one.
        """
        attempt = self._row(attempt_id)
        if attempt is None:
            raise AttemptDeliveryUnavailableError()

        services = self._context.build(self._session)
        try:
            services.answers.save_many(attempt_id, attempt.learner_id, answers)
        except AppError:
            # A rejected answer is UC-03's decision and reaches the learner unchanged; UC-09 has
            # no opinion about whether an answer is well formed.
            raise
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc

        state = self._get_latest_autosaved_state(attempt_id)
        assert state is not None  # noqa: S101 - the attempt was read at the top of this method
        return state

    def _submit_attempt(self, request: SubmissionRequest) -> SubmittedState:
        """Commit the attempt through UC-03's submission service.

        Which of UC-03's two entry points applies is the *only* thing UC-09 decides here. A
        disconnect commits the autosaved state with no completeness requirement; a learner
        confirming goes through the ordinary confirmed path, completeness rules included.
        """
        attempt = self._row(request.attempt_id)
        if attempt is None:
            raise AttemptDeliveryUnavailableError()

        already = attempt.status == "SUBMITTED"
        services = self._context.build(self._session)
        try:
            if request.reason is FormalSubmissionReason.DISCONNECT_AUTO_SUBMIT:
                services.submissions.submit_on_disconnect(attempt)
            elif request.reason is FormalSubmissionReason.TIME_EXPIRED:
                services.submissions.submit_on_expiry(attempt)
            else:
                services.submissions.confirm(
                    attempt, idempotency_key=request.idempotency_key, confirmed=True
                )
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc

        committed = self._row(request.attempt_id)
        assert committed is not None  # noqa: S101
        return SubmittedState(
            attempt_id=request.attempt_id,
            submitted_at=iso_or_none(committed.submitted_at) or "",
            submission_reason=committed.submission_reason,
            answered_questions=self._answered_count(request.attempt_id),
            total_questions=committed.total_questions,
            already_submitted=already,
        )


def attempt_provider_factory(delivery: AppContext):
    """A per-session ``AttemptProvider`` factory bound to UC-03's composition root.

    Exists so UC-09's ``container.py`` never has to name UC-03 — a composition root binds ports and
    is not allowed to know which capability is behind one. ``tests/test_architecture.py`` enforces
    that a cross-capability import lives only in an ``integration/`` package, which is this file.
    """
    return lambda session: FormalAttemptDeliveryAdapter(session, delivery)
