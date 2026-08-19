"""UC-05's outcomes, seen through UC-06's port.

Read-only projection of ``qg_attempt_outcomes``, and the only file in UC-06 that knows UC-05's
schema exists.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.certification.domain.enums import Outcome
from app.modules.certification.models import AttemptOutcome
from app.modules.feedback.integration.certification.port import AttemptOutcomeSummary


class CertificationOutcomeAdapter:
    """:class:`~...certification.port.OutcomePort` over UC-05's tables."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_outcome(self, attempt_id: str) -> AttemptOutcomeSummary | None:
        row = self._session.scalar(
            select(AttemptOutcome).where(AttemptOutcome.attempt_id == attempt_id)
        )
        if row is None:
            return None
        return AttemptOutcomeSummary(
            outcome_id=row.id,
            attempt_id=row.attempt_id,
            outcome=row.outcome,
            passed=row.outcome == str(Outcome.PASS),
            percentage=float(row.percentage),
            pass_mark_percentage=float(row.pass_mark_percentage),
        )
