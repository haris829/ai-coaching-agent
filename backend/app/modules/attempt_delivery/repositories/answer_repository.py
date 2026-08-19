"""Answer persistence.

The upsert here is where autosave idempotency actually lives: ``revision`` advances
only when the stored ``response_hash`` changes, so a client re-saving the same
selection every 30 seconds re-confirms persistence without inflating the revision or
the audit trail.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.attempt_delivery.domain.enums import AnswerSource
from app.modules.attempt_delivery.models import (
    AttemptAnswer,
    AttemptAnswerRevision,
    AttemptQuestion,
)


@dataclass(frozen=True, slots=True)
class AnswerWrite:
    """Everything needed to persist one answer."""

    id: str
    attempt_id: str
    attempt_question_id: str
    question_id: str
    answered: bool
    complete: bool
    canonical: dict[str, Any] | None
    response_hash: str | None
    source: AnswerSource
    saved_at: datetime


@dataclass(frozen=True, slots=True)
class UpsertResult:
    answer: AttemptAnswer
    #: False when the payload matched what was already stored.
    changed: bool


class AnswerRepository:
    """Reads and writes :class:`AttemptAnswer` rows and their audit trail."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, write: AnswerWrite) -> UpsertResult:
        """Insert or update the current answer for a delivered question."""
        existing = self.find(write.attempt_id, write.attempt_question_id)

        if existing is None:
            answer = AttemptAnswer(
                id=write.id,
                attempt_id=write.attempt_id,
                attempt_question_id=write.attempt_question_id,
                question_id=write.question_id,
                answered=write.answered,
                complete=write.complete,
                response=write.canonical,
                response_hash=write.response_hash,
                revision=1,
                source=str(write.source),
                first_saved_at=write.saved_at,
                saved_at=write.saved_at,
            )
            self._session.add(answer)
            self._session.flush()
            self._append_revision(answer)
            return UpsertResult(answer=answer, changed=True)

        unchanged = (
            existing.response_hash == write.response_hash
            and bool(existing.answered) == write.answered
            and bool(existing.complete) == write.complete
        )

        if unchanged:
            # Re-confirm persistence (advancing saved_at) without a new revision.
            existing.saved_at = write.saved_at
            existing.source = str(write.source)
            self._session.flush()
            return UpsertResult(answer=existing, changed=False)

        existing.answered = write.answered
        existing.complete = write.complete
        existing.response = write.canonical
        existing.response_hash = write.response_hash
        existing.revision += 1
        existing.source = str(write.source)
        existing.saved_at = write.saved_at
        self._session.flush()
        self._append_revision(existing)
        return UpsertResult(answer=existing, changed=True)

    def _append_revision(self, answer: AttemptAnswer) -> None:
        self._session.add(
            AttemptAnswerRevision(
                id=f"{answer.attempt_question_id}:{answer.revision}",
                attempt_id=answer.attempt_id,
                attempt_question_id=answer.attempt_question_id,
                revision=answer.revision,
                answered=bool(answer.answered),
                complete=bool(answer.complete),
                response=answer.response,
                source=answer.source,
                saved_at=answer.saved_at,
            )
        )
        self._session.flush()

    def find(self, attempt_id: str, attempt_question_id: str) -> AttemptAnswer | None:
        return self._session.scalars(
            select(AttemptAnswer).where(
                AttemptAnswer.attempt_id == attempt_id,
                AttemptAnswer.attempt_question_id == attempt_question_id,
            )
        ).one_or_none()

    def list_for_attempt(self, attempt_id: str) -> Sequence[AttemptAnswer]:
        return self._session.scalars(
            select(AttemptAnswer)
            .join(AttemptQuestion, AttemptQuestion.id == AttemptAnswer.attempt_question_id)
            .where(AttemptAnswer.attempt_id == attempt_id)
            .order_by(AttemptQuestion.position)
        ).all()

    def count_answered(self, attempt_id: str) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(AttemptAnswer)
                .where(AttemptAnswer.attempt_id == attempt_id, AttemptAnswer.answered.is_(True))
            )
            or 0
        )

    def count_complete(self, attempt_id: str) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(AttemptAnswer)
                .where(AttemptAnswer.attempt_id == attempt_id, AttemptAnswer.complete.is_(True))
            )
            or 0
        )

    def list_revisions(self, attempt_id: str) -> Sequence[AttemptAnswerRevision]:
        """Audit trail of accepted saves, oldest first."""
        return self._session.scalars(
            select(AttemptAnswerRevision)
            .where(AttemptAnswerRevision.attempt_id == attempt_id)
            .order_by(AttemptAnswerRevision.saved_at, AttemptAnswerRevision.revision)
        ).all()
