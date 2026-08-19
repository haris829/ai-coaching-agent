"""UC-03's attempts, seen through UC-07's port.

The anti-corruption layer between the two: UC-03's ORM rows and its frozen JSON snapshots are
translated here, once, and no coaching service or domain function learns anything about them.

Read-only by construction. It issues ``SELECT``s and nothing else — there is no path from here to
creating, timing, unlocking or re-submitting an attempt, and no path to writing a learner's answer.
UC-04's and UC-05's adapters onto the same tables have exactly this shape, for the same reason.

WHAT IS DELIBERATELY DROPPED ON THE WAY THROUGH
-----------------------------------------------
The frozen snapshot carries the answer key: ``isCorrect`` on every option, ``correctPosition`` on
every order item. UC-07's ``DeliveredOption`` and ``DeliveredOrderItem`` have **no field** for
either, so this adapter cannot forward them — not because it remembers not to, but because there is
nowhere to put them. That is the first of the layers described in
``app.modules.coaching.domain.sanitizer``, and it is why the option list can be copied whole: the
coach sees the same four choices the learner saw, in the same order, with nothing marking which was
right.

The delivered snapshot, not today's question bank. Options may have been shuffled and the question
may have been edited or retired since; coaching a learner about a question they never saw would be
worse than no coaching.

WHAT IS DELIBERATELY KEPT
-------------------------
``metadata``. Real delivery records carry blobs whose contents are unknown by definition, and a
sanitiser that is only ever fed clean input has not been tested. The snapshot's ``extra`` is passed
through as untrusted metadata, and the sanitiser drops it wholesale (§13). Two useful things are
lifted *out* of it first — the question's human reference and its frozen topic names — because those
are named, safe fields the coaching context has a home for.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.question_types import QuestionType
from app.core.time import iso_or_none
from app.modules.attempt_delivery.integration.uc02.types import BankQuestion
from app.modules.attempt_delivery.models import AttemptAnswer, AttemptQuestion, QuizAttempt
from app.modules.coaching.domain.errors import UpstreamProviderUnavailableError
from app.modules.coaching.integration.uc03 import (
    AttemptContext,
    AttemptStatus,
    DeliveredOption,
    DeliveredOrderItem,
    DeliveredQuestion,
    LearnerAnswer,
)
from app.modules.coaching.repositories.sqlalchemy import offload


class AttemptDeliveryCoachingAdapter:
    """``AttemptProvider`` over UC-03's tables."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- AttemptProvider --------------------------------------------------

    async def get_attempt(self, attempt_id: str) -> AttemptContext | None:
        return await offload(self._get_attempt, attempt_id)

    async def get_delivered_questions(self, attempt_id: str) -> tuple[DeliveredQuestion, ...]:
        return await offload(self._get_delivered_questions, attempt_id)

    async def get_learner_answers(self, attempt_id: str) -> tuple[LearnerAnswer, ...]:
        return await offload(self._get_learner_answers, attempt_id)

    # ---- synchronous bodies -----------------------------------------------

    def _get_attempt(self, attempt_id: str) -> AttemptContext | None:
        try:
            attempt = self._session.scalar(
                select(QuizAttempt).where(QuizAttempt.id == attempt_id)
            )
        except SQLAlchemyError as exc:
            # "We could not read the attempt" must never degrade into "coaching allowed" (§7).
            raise UpstreamProviderUnavailableError(
                "uc03", attempt_id=attempt_id, cause=exc
            ) from exc
        if attempt is None:
            return None

        snapshot: dict[str, Any] = attempt.configuration_snapshot or {}
        extra: dict[str, Any] = snapshot.get("extra") or {}
        return AttemptContext(
            attempt_id=attempt.id,
            learner_id=attempt.learner_id,
            course_id=attempt.course_id,
            # The course title lives in the configuration snapshot UC-01 froze onto the attempt, so
            # coaching reads it without a second capability's tables. Falling back to the id keeps a
            # coaching prompt truthful rather than blank, exactly as UC-05's adapter does.
            course_name=str(extra.get("courseTitle") or f"Course {attempt.course_id}"),
            quiz_id=attempt.quiz_id,
            status=self._status(attempt.status),
            attempt_number=attempt.attempt_number,
            started_at=iso_or_none(attempt.started_at),
            submitted_at=iso_or_none(attempt.submitted_at),
        )

    def _get_delivered_questions(self, attempt_id: str) -> tuple[DeliveredQuestion, ...]:
        try:
            rows = self._session.scalars(
                select(AttemptQuestion)
                .where(AttemptQuestion.attempt_id == attempt_id)
                .order_by(AttemptQuestion.position)
            ).all()
        except SQLAlchemyError as exc:
            raise UpstreamProviderUnavailableError(
                "uc03", attempt_id=attempt_id, cause=exc
            ) from exc
        return tuple(self._to_delivered(row) for row in rows)

    def _get_learner_answers(self, attempt_id: str) -> tuple[LearnerAnswer, ...]:
        try:
            rows = self._session.scalars(
                select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt_id)
            ).all()
        except SQLAlchemyError as exc:
            raise UpstreamProviderUnavailableError(
                "uc03", attempt_id=attempt_id, cause=exc
            ) from exc
        return tuple(
            LearnerAnswer(
                question_id=row.question_id,
                answered=bool(row.answered),
                response=dict(row.response) if row.response else None,
                saved_at=iso_or_none(row.saved_at),
            )
            for row in rows
        )

    # ---- translation ------------------------------------------------------

    @staticmethod
    def _status(raw: str) -> AttemptStatus:
        """UC-03's lifecycle value as one UC-07 names.

        UC-03 has three states and UC-07 models five, because the coaching gate should be able to
        say what it actually refused. An unrecognised value maps to ``ACTIVE``: the conservative
        reading, since ``ACTIVE`` is refused and the gate must never fail open (§7).
        """
        try:
            return AttemptStatus(raw)
        except ValueError:  # pragma: no cover - only reachable if UC-03 adds a state
            return AttemptStatus.ACTIVE

    def _to_delivered(self, row: AttemptQuestion) -> DeliveredQuestion:
        question_type = QuestionType(row.question_type)
        # Parsed with UC-03's own snapshot type rather than by hand-rolled dict digging, so a change
        # to the snapshot shape surfaces here instead of as a silently empty coaching context.
        frozen = BankQuestion.from_dict(row.question_snapshot)
        extra = dict(frozen.extra or {})

        options: tuple[DeliveredOption, ...] = ()
        order_items: tuple[DeliveredOrderItem, ...] = ()

        if question_type is QuestionType.DRAG_TO_ORDER:
            order_items = tuple(
                # ``position`` is the *delivered* index, never ``item.correct_position``. Carrying
                # the solution order would hand over the answer to the question in full (§12).
                DeliveredOrderItem(item_id=item.item_id, text=item.text, position=index)
                for index, item in enumerate(frozen.order_items, start=1)
            )
        elif question_type is QuestionType.SCENARIO:
            # UC-02 authors one sub-question per scenario, and it carries the options the learner
            # chose from. Same resolution UC-04's adapter makes, so both see the same question.
            primary = frozen.sub_questions[0] if frozen.sub_questions else None
            options = tuple(
                DeliveredOption(option_id=option.option_id, text=option.text, position=index)
                for index, option in enumerate(
                    primary.options if primary is not None else (), start=1
                )
            )
        else:
            options = tuple(
                DeliveredOption(option_id=option.option_id, text=option.text, position=index)
                for index, option in enumerate(frozen.options, start=1)
            )

        return DeliveredQuestion(
            question_id=row.question_id,
            position=row.position,
            question_type=question_type,
            prompt=frozen.prompt,
            scenario_text=frozen.scenario_text,
            options=options,
            order_items=order_items,
            topics=self._topics(extra),
            maximum_marks=float(row.points),
            question_reference=(
                extra.get("reference") if isinstance(extra.get("reference"), str) else None
            ),
            # Everything else the snapshot happened to carry, labelled untrusted and dropped
            # wholesale at the sanitisation boundary (§13).
            metadata=extra,
        )

    @staticmethod
    def _topics(extra: dict[str, Any]) -> tuple[str, ...]:
        """The question's frozen topic names, if UC-02's delivery seam recorded any.

        A topic is what a coaching session is about, what a knowledge gap is recorded against, and
        what a review item is labelled with. It is not answer-bearing: "Reporting concerns" says
        what the question covered, not which option was right.
        """
        names = extra.get("topicNames")
        if not isinstance(names, list):
            return ()
        return tuple(name.strip() for name in names if isinstance(name, str) and name.strip())
