"""The retake request — reservation, lineage and record, in one entity.

It is easy to read this as bookkeeping. It is not: it is the mechanism that makes §15 and §16
true, and it does three jobs at once.

**A reservation.** Written *before* UC-03 is asked for an attempt, holding
``(learner_id, quiz_id, attempt_number)`` under a uniqueness constraint. Two simultaneous retake
requests both compute the same next attempt number; only one insert survives, so a learner cannot
end up with more attempts than their allowance no matter how the requests interleave. A RESERVED
row counts as an attempt used even though UC-03 has not created one yet, which closes the window
between the allowance check and the delivery.

**An idempotency record.** Keyed by ``retake:<learner>:<quiz>:<previous attempt>``. A client that
retries after a timeout produces the same key, finds the completed record and is handed the
attempt that already exists rather than a second one.

**The lineage.** ``previous_attempt_id`` → ``attempt_id`` is the retake relationship §10 asks
for. It lives here rather than in a new structure because UC-03 already owns attempts and their
numbering; a separate lineage table would be a second place for the same fact to be wrong.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not hold a score, a pass/fail status, an answer or a question snapshot. Those belong to
UC-03/UC-04/UC-05 and are read through ports when history is assembled. Nothing about a previous
attempt is copied into this record, so nothing about a previous attempt can be changed by writing
one (§3).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.modules.retakes.domain.anomalies import RetakeAnomaly
from app.modules.retakes.domain.enums import ConfigurationVersionSource, RetakeRequestStatus


@dataclass(frozen=True, slots=True)
class RetakeRequest:
    """One retake, from reservation through to the attempt it produced.

    Frozen, and every transition returns a new instance, so a stored record cannot be edited in
    place by a caller that happens to hold a reference to it.
    """

    retake_id: str
    idempotency_key: str
    learner_id: str
    course_id: str
    quiz_id: str
    #: The completed attempt this retake follows. Immutable; never modified by this record.
    previous_attempt_id: str
    #: The slot this request reserved. Unique per learner + quiz among non-FAILED requests.
    attempt_number: int
    configuration_version_id: str
    configuration_version_source: ConfigurationVersionSource
    status: RetakeRequestStatus
    requested_at: str
    updated_at: str
    configuration_version_number: int | None = None
    #: Set when UC-03 has created the attempt. ``None`` while RESERVED or after a failure.
    attempt_id: str | None = None
    completed_at: str | None = None
    #: Snapshot of the plan the retake was created under, so "why did I see that question again?"
    #: is answerable later without re-deriving the bank state as it was at the time.
    question_plan: dict[str, Any] | None = None
    question_set_difference: dict[str, Any] | None = None
    anomalies: tuple[RetakeAnomaly, ...] = field(default_factory=tuple)
    failure_code: str | None = None
    failure_message: str | None = None
    #: Incremented each time a failed request is retried. Distinguishes "retried twice and
    #: eventually worked" from "worked first time" in the audit trail.
    attempt_count: int = 1

    # ---- lifecycle ------------------------------------------------------

    @property
    def reserved(self) -> bool:
        return self.status is RetakeRequestStatus.RESERVED

    @property
    def completed(self) -> bool:
        return self.status is RetakeRequestStatus.COMPLETED

    @property
    def failed(self) -> bool:
        return self.status is RetakeRequestStatus.FAILED

    @property
    def holds_attempt_slot(self) -> bool:
        """Whether this record still consumes one of the learner's attempts.

        A FAILED request does not: nothing was created, so nothing was spent. This is what makes
        a failed retake safely retryable rather than a silently lost attempt.
        """
        return self.status in {RetakeRequestStatus.RESERVED, RetakeRequestStatus.COMPLETED}

    def completed_with(
        self,
        *,
        attempt_id: str,
        at: str,
        question_set_difference: dict[str, Any] | None = None,
        anomalies: tuple[RetakeAnomaly, ...] = (),
    ) -> RetakeRequest:
        return replace(
            self,
            status=RetakeRequestStatus.COMPLETED,
            attempt_id=attempt_id,
            completed_at=at,
            updated_at=at,
            question_set_difference=question_set_difference,
            anomalies=self.anomalies + anomalies,
            failure_code=None,
            failure_message=None,
        )

    def failed_with(self, *, code: str, message: str, at: str) -> RetakeRequest:
        """Release the reservation and record why.

        The attempt id stays ``None``: a failed retake produced no attempt, and recording one
        would corrupt the used count in exactly the way §14 forbids.
        """
        return replace(
            self,
            status=RetakeRequestStatus.FAILED,
            failure_code=code,
            failure_message=message,
            updated_at=at,
            attempt_id=None,
        )

    def reopened(self, *, at: str) -> RetakeRequest:
        """Take the slot again for a retry of a failed request."""
        return replace(
            self,
            status=RetakeRequestStatus.RESERVED,
            failure_code=None,
            failure_message=None,
            updated_at=at,
            attempt_count=self.attempt_count + 1,
        )

    # ---- serialisation ---------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "retake_id": self.retake_id,
            "learner_id": self.learner_id,
            "course_id": self.course_id,
            "quiz_id": self.quiz_id,
            "previous_attempt_id": self.previous_attempt_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "status": self.status.value,
            "configuration_version_id": self.configuration_version_id,
            "configuration_version_number": self.configuration_version_number,
            "configuration_version_source": self.configuration_version_source.value,
            "requested_at": self.requested_at,
            "completed_at": self.completed_at,
            "updated_at": self.updated_at,
            "question_plan": self.question_plan,
            "question_set_difference": self.question_set_difference,
            "anomalies": [item.as_dict() for item in self.anomalies],
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
            "attempt_count": self.attempt_count,
        }
