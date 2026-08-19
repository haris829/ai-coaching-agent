"""The attempt's frozen question set."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.attempt_delivery.models import AttemptQuestion


class AttemptQuestionRepository:
    """Reads and writes :class:`AttemptQuestion` rows."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_all(self, questions: Sequence[AttemptQuestion]) -> None:
        """Persist the whole delivered question set.

        Always called inside the attempt-creation transaction: an attempt without its
        frozen question set must never be observable.
        """
        self._session.add_all(list(questions))
        self._session.flush()

    def list_for_attempt(self, attempt_id: str) -> Sequence[AttemptQuestion]:
        return self._session.scalars(
            select(AttemptQuestion)
            .where(AttemptQuestion.attempt_id == attempt_id)
            .order_by(AttemptQuestion.position)
        ).all()

    def find_by_question_id(self, attempt_id: str, question_id: str) -> AttemptQuestion | None:
        return self._session.scalars(
            select(AttemptQuestion).where(
                AttemptQuestion.attempt_id == attempt_id,
                AttemptQuestion.question_id == question_id,
            )
        ).one_or_none()

    def find_by_position(self, attempt_id: str, position: int) -> AttemptQuestion | None:
        return self._session.scalars(
            select(AttemptQuestion).where(
                AttemptQuestion.attempt_id == attempt_id, AttemptQuestion.position == position
            )
        ).one_or_none()

    def count_for_attempt(self, attempt_id: str) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(AttemptQuestion)
                .where(AttemptQuestion.attempt_id == attempt_id)
            )
            or 0
        )
