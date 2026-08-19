"""Course enrolment, seen through UC-03's port.

Replaces the provisional adapter that read ``ext_enrolments``. It now reads the platform placeholder
table (``qa_enrolments``, beside ``qa_users``), which is the honest home for data the company owns.

UC-03 asks one question here — "may this learner attempt a quiz on this course?" — and the answer is
a yes/no plus a status. Which statuses qualify is a platform business rule, so it lives in
:data:`app.modules.identity.enums.ATTEMPT_ELIGIBLE_ENROLMENT_STATUSES` rather than in this adapter
or in a UC-03 service.

When the company's enrolment service arrives, this class is reimplemented against it and nothing in
UC-03 changes.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.time import to_iso
from app.modules.attempt_delivery.integration.enrolment.port import Enrolment
from app.modules.identity.enums import EnrolmentStatus
from app.modules.identity.models import Enrolment as EnrolmentRow


class PlatformEnrolmentAdapter:
    """:class:`~...enrolment.port.EnrolmentPort` over the platform placeholder table."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_enrolment(self, learner_id: str, course_id: str) -> Enrolment | None:
        row = self._session.get(EnrolmentRow, (str(learner_id), str(course_id)))
        if row is None:
            return None
        return Enrolment(
            learner_id=row.learner_id,
            course_id=row.course_id,
            status=EnrolmentStatus(row.status),
            enrolled_at=to_iso(row.enrolled_at),
        )
