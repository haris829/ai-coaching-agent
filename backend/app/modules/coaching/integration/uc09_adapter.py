"""UC-09, behind UC-07's ``FormalAssessmentPolicyPort``.

A single read of ``qs_formal_attempts`` for the states UC-09 calls open. It deliberately does not
go through UC-09's ``AiCoachingPolicyService``: that service writes an audit event and shapes a
refusal, which is right when UC-09 is answering for itself, but here UC-07 owns the refusal — it
has its own eligibility vocabulary, its own message and its own error envelope, and two modules
each rendering the same refusal differently is how a learner ends up with two explanations.

What UC-07 needs is the fact. This adapter supplies the fact.

**An unreadable UC-09 raises.** ``ProviderUnavailableError`` propagates through UC-07's authoriser
exactly as a UC-03, UC-04 or UC-06 failure does, and for the same reason: "we could not confirm
this learner is not sitting an exam" must never become "coaching allowed".
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.async_db import offload
from app.core.errors import ProviderUnavailableError
from app.modules.coaching.integration.uc09 import FormalAssessmentStatus
from app.modules.formal_assessment.domain.enums import OPEN_FORMAL_STATES
from app.modules.formal_assessment.models import FormalAttemptRow

#: Read from UC-09's own constant rather than restated, so "open" cannot come to mean two things.
_OPEN_STATE_VALUES = tuple(state.value for state in OPEN_FORMAL_STATES)


class FormalAssessmentCoachingAdapter:
    """``FormalAssessmentPolicyPort`` over UC-09's ``qs_formal_attempts``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get_status_for_learner(self, learner_id: str) -> FormalAssessmentStatus:
        return await offload(self._get_status_for_learner, learner_id)

    def _get_status_for_learner(self, learner_id: str) -> FormalAssessmentStatus:
        try:
            row = self._session.scalar(
                select(FormalAttemptRow)
                .where(
                    FormalAttemptRow.learner_id == learner_id,
                    FormalAttemptRow.state.in_(_OPEN_STATE_VALUES),
                )
                # Oldest first, so the sitting named in the refusal is the one that has been
                # running longest rather than an arbitrary one.
                .order_by(FormalAttemptRow.created_at)
            )
        except SQLAlchemyError as exc:
            raise ProviderUnavailableError("uc09", cause=exc) from exc

        if row is None:
            return FormalAssessmentStatus(in_progress=False)
        return FormalAssessmentStatus(
            in_progress=True, formal_attempt_id=row.id, quiz_id=row.quiz_id
        )
