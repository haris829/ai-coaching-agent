"""The CPD system, as implemented locally until the company's exists.

It acknowledges the record and returns a stable reference derived from the attempt id. Nothing is
sent anywhere, and that is stated rather than dressed up: pretending to call an external system
would make the retry tests prove something that is not true.

Because the reference is deterministic, a retried synchronisation is recognisable as the same event
rather than as a second one -- the same property the local certificate service has, and for the same
reason.
"""

from __future__ import annotations

import hashlib

from app.modules.certification.integration.cpd.port import CpdSyncAck, CpdSyncRecord

#: Prefix for locally generated CPD references, so one is never mistaken for the real system's.
LOCAL_PREFIX = "CPD-LOCAL"


def reference_for(attempt_id: str) -> str:
    """A stable CPD reference for one attempt."""
    digest = hashlib.sha256(f"cpd:{attempt_id}".encode()).hexdigest()[:10].upper()
    return f"{LOCAL_PREFIX}-{digest}"


class LocalCpdSyncService:
    """:class:`~...cpd.port.CpdSyncPort`, implemented in-process."""

    __slots__ = ()

    def synchronise(self, record: CpdSyncRecord) -> CpdSyncAck:
        return CpdSyncAck(
            external_reference=reference_for(record.attempt_id),
            metadata={
                "courseName": record.course_name,
                "attemptDate": record.attempt_date,
                "scorePercentage": record.score_percentage,
                "passed": record.passed,
                "syncedBy": "local-cpd-service",
            },
        )
