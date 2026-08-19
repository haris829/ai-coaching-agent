"""UC-03's attempts, seen through UC-04's port.

The anti-corruption layer between the two: UC-03's ORM rows and its frozen JSON snapshots are
translated here, once, and no UC-04 service or domain function learns anything about them.

Read-only by construction. It issues ``SELECT``s and nothing else -- there is no path from here to
creating, timing, unlocking or re-submitting an attempt. UC-01's attempt-statistics adapter has the
same shape, for the same reason.

Two translations worth reading carefully:

**Options and order items.** UC-03 delivers choice options and drag-to-order items as separate
collections on its frozen snapshot; UC-04 marks both as a single ordered list of options, with
``correct_position`` set only for an ordering question.

**SCENARIO.** UC-03 delivers a vignette plus sub-questions. UC-02 authors exactly one sub-question
per scenario, and that one carries the configured primary answer -- so its options are the options
UC-04 marks, and its id is the primary sub-question id.

The frozen snapshot is parsed with UC-03's own :class:`BankQuestion`, not with hand-rolled ``dict``
digging, so a change to the snapshot shape shows up as a type error here rather than as silently
missing marks.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.attempt_delivery.domain.enums import (
    LOCKED_ATTEMPT_STATUSES,
    SubmissionState,
)
from app.modules.attempt_delivery.integration.uc02.types import BankQuestion
from app.modules.attempt_delivery.models import (
    AttemptAnswer,
    AttemptQuestion,
    AttemptSubmission,
    QuizAttempt,
)
from app.modules.scoring.domain.enums import QuestionType
from app.modules.scoring.integration.attempt_delivery.types import (
    DeliveredOption,
    DeliveredQuestion,
    SubmittedAttempt,
)

logger = get_logger(__name__)

_LOCKED = [str(status) for status in LOCKED_ATTEMPT_STATUSES]


class AttemptDeliveryAdapter:
    """:class:`~...attempt_delivery.port.AttemptSourcePort` over UC-03's tables."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- AttemptSourcePort ------------------------------------------------

    def get_attempt(
        self, attempt_id: str, *, learner_id: str | None = None
    ) -> SubmittedAttempt | None:
        statement = select(QuizAttempt).where(QuizAttempt.id == attempt_id)
        if learner_id is not None:
            # Ownership is part of the query, so a caller cannot forget to check it.
            statement = statement.where(QuizAttempt.learner_id == str(learner_id))
        attempt = self._session.scalar(statement)
        if attempt is None:
            return None
        return self._to_port(attempt)

    def list_submitted_attempt_ids(
        self, *, learner_id: str, quiz_id: str | None = None
    ) -> list[str]:
        statement = select(QuizAttempt.id).where(
            QuizAttempt.learner_id == str(learner_id),
            QuizAttempt.status.in_(_LOCKED),
        )
        if quiz_id is not None:
            statement = statement.where(QuizAttempt.quiz_id == str(quiz_id))
        rows = self._session.scalars(statement.order_by(QuizAttempt.attempt_number.desc())).all()
        return list(rows)

    # ---- translation ------------------------------------------------------

    def _to_port(self, attempt: QuizAttempt) -> SubmittedAttempt:
        questions = self._session.scalars(
            select(AttemptQuestion)
            .where(AttemptQuestion.attempt_id == attempt.id)
            .order_by(AttemptQuestion.position)
        ).all()
        answers = {
            answer.attempt_question_id: answer
            for answer in self._session.scalars(
                select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id)
            ).all()
        }
        submission_id = self._session.scalar(
            select(AttemptSubmission.id).where(
                AttemptSubmission.attempt_id == attempt.id,
                AttemptSubmission.state == str(SubmissionState.SUBMITTED),
            )
        )

        snapshot = attempt.configuration_snapshot or {}
        return SubmittedAttempt(
            attempt_id=attempt.id,
            learner_id=attempt.learner_id,
            course_id=attempt.course_id,
            quiz_id=attempt.quiz_id,
            attempt_number=attempt.attempt_number,
            status=attempt.status,
            locked=attempt.status in _LOCKED,
            configuration_version_id=attempt.configuration_version_id,
            configuration_version_number=attempt.configuration_version_number,
            # The pass mark of the version the attempt is locked to. Reading it from the snapshot,
            # not from UC-01, is what makes a reconfiguration mid-course unable to move the bar.
            pass_mark_percentage=float(snapshot.get("passMarkPercentage") or 0.0),
            started_at=attempt.started_at,
            submitted_at=attempt.submitted_at,
            submission_id=submission_id,
            configuration_snapshot=dict(snapshot),
            questions=tuple(
                self._to_delivered(question, answers.get(question.id)) for question in questions
            ),
        )

    def _to_delivered(
        self, question: AttemptQuestion, answer: AttemptAnswer | None
    ) -> DeliveredQuestion:
        question_type = QuestionType(question.question_type)
        frozen = BankQuestion.from_dict(question.question_snapshot)

        options: tuple[DeliveredOption, ...]
        sub_question_ids: tuple[str, ...] = ()

        if question_type is QuestionType.DRAG_TO_ORDER:
            options = tuple(
                DeliveredOption(
                    option_id=item.item_id, text=item.text, correct_position=item.correct_position
                )
                for item in frozen.order_items
            )
        elif question_type is QuestionType.SCENARIO:
            sub_question_ids = tuple(sub.sub_question_id for sub in frozen.sub_questions)
            primary = frozen.sub_questions[0] if frozen.sub_questions else None
            options = tuple(
                DeliveredOption(
                    option_id=option.option_id, text=option.text, is_correct=option.is_correct
                )
                for option in (primary.options if primary is not None else ())
            )
        else:
            options = tuple(
                DeliveredOption(
                    option_id=option.option_id, text=option.text, is_correct=option.is_correct
                )
                for option in frozen.options
            )

        return DeliveredQuestion(
            attempt_question_id=question.id,
            question_id=question.question_id,
            question_version=question.question_version,
            question_type=question_type,
            position=question.position,
            max_marks=float(question.points),
            prompt=frozen.prompt,
            scenario_text=frozen.scenario_text,
            options=options,
            sub_question_ids=sub_question_ids,
            answered=bool(answer.answered) if answer is not None else False,
            complete=bool(answer.complete) if answer is not None else False,
            response=answer.response if answer is not None else None,
            extra=dict(frozen.extra or {}),
        )
