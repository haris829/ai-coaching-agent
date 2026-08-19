"""Submission persistence.

Idempotency is enforced by the database rather than by a read-then-write check:
:meth:`insert_pending` relies on ``ux_submission_idempotency`` to reject a duplicate
claim, and ``ux_submission_single_success`` makes more than one successful submission
per attempt impossible even under a race.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.attempt_delivery.domain.enums import SubmissionReason, SubmissionState
from app.modules.attempt_delivery.models import AttemptSubmission


class SubmissionRepository:
    """Reads and writes :class:`AttemptSubmission` rows."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def insert_pending(
        self,
        *,
        submission_id: str,
        attempt_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        submission_reason: SubmissionReason,
        answered_count: int,
        total_questions: int,
        now: datetime,
    ) -> AttemptSubmission:
        """Claim a submission slot for ``(attempt_id, idempotency_key)`` as PENDING.

        Raises :class:`sqlalchemy.exc.IntegrityError` when another request already
        claimed the same key; the caller resolves that by reading the existing row.
        """
        submission = AttemptSubmission(
            id=submission_id,
            attempt_id=attempt_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            state=str(SubmissionState.PENDING),
            submission_reason=str(submission_reason),
            attempt_count=1,
            answered_count=answered_count,
            total_questions=total_questions,
            requested_at=now,
            last_attempted_at=now,
        )
        self._session.add(submission)
        self._session.flush()
        return submission

    # ---- lookup -----------------------------------------------------------

    def get(self, submission_id: str) -> AttemptSubmission | None:
        return self._session.get(AttemptSubmission, submission_id)

    def find_by_idempotency_key(
        self, attempt_id: str, idempotency_key: str
    ) -> AttemptSubmission | None:
        return self._session.scalars(
            select(AttemptSubmission).where(
                AttemptSubmission.attempt_id == attempt_id,
                AttemptSubmission.idempotency_key == idempotency_key,
            )
        ).one_or_none()

    def find_submitted(self, attempt_id: str) -> AttemptSubmission | None:
        """The single successful submission for an attempt, if any."""
        return self._session.scalars(
            select(AttemptSubmission).where(
                AttemptSubmission.attempt_id == attempt_id,
                AttemptSubmission.state == str(SubmissionState.SUBMITTED),
            )
        ).one_or_none()

    def find_pending(self, attempt_id: str) -> AttemptSubmission | None:
        return self._session.scalars(
            select(AttemptSubmission)
            .where(
                AttemptSubmission.attempt_id == attempt_id,
                AttemptSubmission.state == str(SubmissionState.PENDING),
            )
            .order_by(AttemptSubmission.requested_at)
            .limit(1)
        ).first()

    def list_for_attempt(self, attempt_id: str) -> Sequence[AttemptSubmission]:
        return self._session.scalars(
            select(AttemptSubmission)
            .where(AttemptSubmission.attempt_id == attempt_id)
            .order_by(AttemptSubmission.requested_at)
        ).all()

    def count_for_attempt(self, attempt_id: str, state: SubmissionState | None = None) -> int:
        statement = (
            select(func.count())
            .select_from(AttemptSubmission)
            .where(AttemptSubmission.attempt_id == attempt_id)
        )
        if state is not None:
            statement = statement.where(AttemptSubmission.state == str(state))
        return self._session.scalar(statement) or 0

    # ---- mutation ---------------------------------------------------------

    def record_retry(self, submission_id: str, now: datetime) -> None:
        """Record a retry of an existing submission record."""
        self._session.execute(
            update(AttemptSubmission)
            .where(AttemptSubmission.id == submission_id)
            .values(
                state=str(SubmissionState.PENDING),
                attempt_count=AttemptSubmission.attempt_count + 1,
                last_attempted_at=now,
                failure_code=None,
                failure_message=None,
            )
        )
        self._session.flush()
        self._session.expire_all()

    def mark_submitted(
        self,
        *,
        submission_id: str,
        answered_count: int,
        response_snapshot: dict[str, Any],
        downstream_reference: str | None,
        now: datetime,
    ) -> bool:
        """Mark the submission complete.

        Guarded on the row still being PENDING and protected by
        ``ux_submission_single_success``, so two concurrent completions cannot both
        land.
        """
        result = self._session.execute(
            update(AttemptSubmission)
            .where(
                AttemptSubmission.id == submission_id,
                AttemptSubmission.state == str(SubmissionState.PENDING),
            )
            .values(
                state=str(SubmissionState.SUBMITTED),
                answered_count=answered_count,
                response_snapshot=response_snapshot,
                downstream_reference=downstream_reference,
                failure_code=None,
                failure_message=None,
                last_attempted_at=now,
                completed_at=now,
            )
        )
        self._session.flush()
        self._session.expire_all()
        return result.rowcount == 1

    def mark_pending_failure(
        self, submission_id: str, failure_code: str, failure_message: str, now: datetime
    ) -> None:
        """Record a transient failure: the submission stays retriable."""
        self._session.execute(
            update(AttemptSubmission)
            .where(AttemptSubmission.id == submission_id)
            .values(
                state=str(SubmissionState.PENDING),
                failure_code=failure_code,
                failure_message=failure_message,
                last_attempted_at=now,
            )
        )
        self._session.flush()
        self._session.expire_all()

    def mark_failed(
        self, submission_id: str, failure_code: str, failure_message: str, now: datetime
    ) -> None:
        """Record a permanent failure: the submission will not be retried."""
        self._session.execute(
            update(AttemptSubmission)
            .where(AttemptSubmission.id == submission_id)
            .values(
                state=str(SubmissionState.FAILED),
                failure_code=failure_code,
                failure_message=failure_message,
                last_attempted_at=now,
                completed_at=None,
            )
        )
        self._session.flush()
        self._session.expire_all()
