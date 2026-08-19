"""Question flag persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.attempt_delivery.models import AttemptQuestion, AttemptQuestionFlag


class FlagRepository:
    """Reads and writes :class:`AttemptQuestionFlag` rows."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def set_flag(
        self,
        *,
        flag_id: str,
        attempt_id: str,
        attempt_question_id: str,
        question_id: str,
        flagged: bool,
        now: datetime,
    ) -> AttemptQuestionFlag:
        """Set the flag state for a delivered question.

        Idempotent: setting the same state twice is accepted and simply refreshes
        ``updated_at``, so a retrying client never errors.
        """
        existing = self.find(attempt_id, attempt_question_id)

        if existing is None:
            flag = AttemptQuestionFlag(
                id=flag_id,
                attempt_id=attempt_id,
                attempt_question_id=attempt_question_id,
                question_id=question_id,
                flagged=flagged,
                flagged_at=now if flagged else None,
                updated_at=now,
            )
            self._session.add(flag)
            self._session.flush()
            return flag

        # Preserve the original flagging instant when re-flagging an already flagged
        # question, so "when did the learner mark this" stays meaningful.
        if flagged:
            existing.flagged_at = existing.flagged_at if existing.flagged else now
        else:
            existing.flagged_at = None
        existing.flagged = flagged
        existing.updated_at = now
        self._session.flush()
        return existing

    def find(self, attempt_id: str, attempt_question_id: str) -> AttemptQuestionFlag | None:
        return self._session.scalars(
            select(AttemptQuestionFlag).where(
                AttemptQuestionFlag.attempt_id == attempt_id,
                AttemptQuestionFlag.attempt_question_id == attempt_question_id,
            )
        ).one_or_none()

    def list_for_attempt(self, attempt_id: str) -> Sequence[AttemptQuestionFlag]:
        return self._session.scalars(
            select(AttemptQuestionFlag)
            .join(AttemptQuestion, AttemptQuestion.id == AttemptQuestionFlag.attempt_question_id)
            .where(AttemptQuestionFlag.attempt_id == attempt_id)
            .order_by(AttemptQuestion.position)
        ).all()

    def count_flagged(self, attempt_id: str) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(AttemptQuestionFlag)
                .where(
                    AttemptQuestionFlag.attempt_id == attempt_id,
                    AttemptQuestionFlag.flagged.is_(True),
                )
            )
            or 0
        )
