"""UC-04's results, seen through UC-05's port.

A read-only projection of ``qr_attempt_results`` onto UC-05's contract type. It is the only file in
UC-05 that knows UC-04's schema exists.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.certification.integration.scoring.port import ConfirmedResult
from app.modules.scoring.domain.enums import ResultStatus
from app.modules.scoring.models import AttemptResult


class ScoringResultAdapter:
    """:class:`~...scoring.port.ScoreResultPort` over UC-04's tables."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_result(self, attempt_id: str) -> ConfirmedResult | None:
        row = self._session.scalar(
            select(AttemptResult).where(AttemptResult.attempt_id == attempt_id)
        )
        if row is None:
            return None
        return ConfirmedResult(
            result_id=row.id,
            attempt_id=row.attempt_id,
            learner_id=row.learner_id,
            course_id=row.course_id,
            quiz_id=row.quiz_id,
            attempt_number=row.attempt_number,
            configuration_version_id=row.configuration_version_id,
            percentage=float(row.percentage),
            total_marks=float(row.total_marks),
            maximum_marks=float(row.maximum_marks),
            pass_mark_percentage=float(row.pass_mark_percentage),
            status=row.status,
            # UC-04 decides what confirmed means; UC-05 only reads the answer.
            confirmed=row.status == str(ResultStatus.SCORED),
            submitted_at=row.submitted_at,
        )
