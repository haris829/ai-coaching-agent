"""Attempt persistence.

Lifecycle transitions are written as *conditional* updates (compare-and-set on
``status``) rather than read-then-write. That is what resolves the answer-save /
timer-expiry race without a second round trip: the loser of the race sees zero rows
affected and can react, instead of both writers believing they won.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.attempt_delivery.domain.enums import (
    OPEN_ATTEMPT_STATUSES,
    AttemptStatus,
    SubmissionReason,
)
from app.modules.attempt_delivery.models import QuizAttempt

_OPEN_STATUS_VALUES: list[str] = sorted(str(status) for status in OPEN_ATTEMPT_STATUSES)


class AttemptRepository:
    """Reads and writes :class:`QuizAttempt` rows."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- creation ---------------------------------------------------------

    def add(self, attempt: QuizAttempt) -> QuizAttempt:
        self._session.add(attempt)
        self._session.flush()
        return attempt

    # ---- lookup -----------------------------------------------------------

    def get(self, attempt_id: str) -> QuizAttempt | None:
        return self._session.get(QuizAttempt, attempt_id)

    def get_for_learner(self, attempt_id: str, learner_id: str) -> QuizAttempt | None:
        """Scope the lookup to the owning learner.

        Callers use this so one learner can never read or mutate another's attempt;
        authorisation does not depend on each handler remembering to check.
        """
        return self._session.scalars(
            select(QuizAttempt).where(
                QuizAttempt.id == attempt_id, QuizAttempt.learner_id == learner_id
            )
        ).one_or_none()

    def find_open(self, learner_id: str, quiz_id: str) -> QuizAttempt | None:
        """The learner's single open (ACTIVE or SUBMISSION_PENDING) attempt."""
        return self._session.scalars(
            select(QuizAttempt).where(
                QuizAttempt.learner_id == learner_id,
                QuizAttempt.quiz_id == quiz_id,
                QuizAttempt.status.in_(_OPEN_STATUS_VALUES),
            )
        ).one_or_none()

    def list_for_learner_and_quiz(self, learner_id: str, quiz_id: str) -> Sequence[QuizAttempt]:
        return self._session.scalars(
            select(QuizAttempt)
            .where(QuizAttempt.learner_id == learner_id, QuizAttempt.quiz_id == quiz_id)
            .order_by(QuizAttempt.attempt_number)
        ).all()

    def count_for_learner_and_quiz(self, learner_id: str, quiz_id: str) -> int:
        """Attempts the learner has consumed for this quiz.

        Every created attempt counts, in progress or not, because that is what the
        maximum-attempts rule limits.
        """
        return (
            self._session.scalar(
                select(func.count())
                .select_from(QuizAttempt)
                .where(QuizAttempt.learner_id == learner_id, QuizAttempt.quiz_id == quiz_id)
            )
            or 0
        )

    def next_attempt_number(self, learner_id: str, quiz_id: str) -> int:
        highest = self._session.scalar(
            select(func.max(QuizAttempt.attempt_number)).where(
                QuizAttempt.learner_id == learner_id, QuizAttempt.quiz_id == quiz_id
            )
        )
        return (highest or 0) + 1

    def find_expired(self, now: datetime, limit: int = 100) -> Sequence[QuizAttempt]:
        """Attempts past their expiry but still ACTIVE. Drives the expiry sweep."""
        return self._session.scalars(
            select(QuizAttempt)
            .where(
                QuizAttempt.status == str(AttemptStatus.ACTIVE),
                QuizAttempt.expires_at.is_not(None),
                QuizAttempt.expires_at <= now,
            )
            .order_by(QuizAttempt.expires_at)
            .limit(limit)
        ).all()

    # ---- mutation ---------------------------------------------------------

    def update_cursor(self, attempt_id: str, position: int, now: datetime) -> None:
        self._session.execute(
            update(QuizAttempt)
            .where(QuizAttempt.id == attempt_id)
            .values(current_position=position, last_activity_at=now, updated_at=now)
        )
        self._session.flush()

    def touch_activity(self, attempt_id: str, now: datetime) -> None:
        self._session.execute(
            update(QuizAttempt)
            .where(QuizAttempt.id == attempt_id)
            .values(last_activity_at=now, updated_at=now)
        )
        self._session.flush()

    def lock_for_submission(
        self,
        attempt_id: str,
        *,
        status: AttemptStatus,
        reason: SubmissionReason,
        submitted_at: datetime,
        now: datetime,
    ) -> bool:
        """Lock the attempt: the learner has committed it, answers become immutable.

        The ``status == ACTIVE`` predicate makes this a compare-and-set. It returns
        False when another request (or the expiry sweep) locked the attempt first.
        """
        finalised_at = now if status is AttemptStatus.SUBMITTED else None
        result = self._session.execute(
            update(QuizAttempt)
            .where(QuizAttempt.id == attempt_id, QuizAttempt.status == str(AttemptStatus.ACTIVE))
            .values(
                status=str(status),
                submitted_at=submitted_at,
                submission_reason=str(reason),
                finalised_at=finalised_at,
                last_activity_at=now,
                updated_at=now,
            )
        )
        self._session.flush()
        self._session.expire_all()
        return result.rowcount == 1

    def mark_submitted(self, attempt_id: str, now: datetime) -> bool:
        """Complete a previously pending submission."""
        result = self._session.execute(
            update(QuizAttempt)
            .where(
                QuizAttempt.id == attempt_id,
                QuizAttempt.status.in_(
                    [str(AttemptStatus.ACTIVE), str(AttemptStatus.SUBMISSION_PENDING)]
                ),
            )
            .values(
                status=str(AttemptStatus.SUBMITTED),
                finalised_at=now,
                last_activity_at=now,
                updated_at=now,
            )
        )
        self._session.flush()
        self._session.expire_all()
        return result.rowcount == 1

    def unlock_after_failure(self, attempt_id: str, now: datetime) -> bool:
        """Return a locked attempt to ACTIVE after a permanent submission failure.

        Without this the learner would be stranded with an attempt they can neither
        edit nor submit.
        """
        result = self._session.execute(
            update(QuizAttempt)
            .where(
                QuizAttempt.id == attempt_id,
                QuizAttempt.status == str(AttemptStatus.SUBMISSION_PENDING),
            )
            .values(
                status=str(AttemptStatus.ACTIVE),
                submitted_at=None,
                submission_reason=None,
                finalised_at=None,
                last_activity_at=now,
                updated_at=now,
            )
        )
        self._session.flush()
        self._session.expire_all()
        return result.rowcount == 1
