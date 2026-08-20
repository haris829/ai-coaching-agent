"""The human review record (§9, §10, §13).

    PASS  ->  PENDING_REVIEW (persisted)  ->  queue notified (best effort)
                    |                                    |
                    |                                    +-- queue down? PENDING, retriable
                    v
              assessor opens -> IN_REVIEW -> APPROVED | REQUIRES_FURTHER_REVIEW

THE RECORD IS THE TRUTH; THE QUEUE IS A NOTIFICATION
----------------------------------------------------
This is the outbox pattern, and it is the whole answer to §13. The review is persisted with
``publish_state = PENDING`` *before* any queue is touched. If the queue is unavailable, the review
still exists, still appears in ``list_pending``, is still fully reviewable through the API, and the
certificate is still blocked. A queue outage therefore delays an assessor's *notification* and
nothing else — the assessment cannot go missing, because the thing that could go missing was never
where the assessment lived.

``publish_attempts`` and ``last_publish_error`` are on the record so a retry is a decision made from
persisted facts rather than from a counter in a process that may have restarted.

ONE REVIEW PER FORMAL ATTEMPT, ENFORCED BY A CONSTRAINT
-------------------------------------------------------
``formal_attempt_id`` is unique. That single constraint closes two of §20's races at once: the same
pass cannot create two reviews, and the same pending assessment cannot be inserted into the queue
twice, because the queue entry is derived from the review and there is only ever one review.

A DECISION IS FINAL
-------------------
``decide`` refuses to act on a review that already has a decision. Two assessors deciding
simultaneously resolve to the first decision plus a conflict for the second, never to an overwrite —
and never to a review that is APPROVED in the record while an escalation is what actually happened.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.modules.formal_assessment.domain.enums import (
    DECIDED_REVIEW_STATES,
    AssessorDecision,
    QueuePublishState,
    ReviewState,
)
from app.modules.formal_assessment.domain.errors import ReviewAlreadyDecidedError

#: Bounded so an assessor's note cannot become an unbounded column in the company database.
MAX_DECISION_NOTES_LENGTH = 4000


@dataclass(frozen=True, slots=True)
class AssessorDecisionRecord:
    """One assessor's decision, and who made it.

    Immutable once written. "Who approved this certificate?" is the question the whole use case
    exists to be able to answer, so the decision is a record with a name on it rather than a status
    change with a timestamp.
    """

    decision: AssessorDecision
    decided_by: str
    decided_at: str
    notes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class FormalReview:
    """The review of one passing formal attempt."""

    review_id: str
    formal_attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    attempt_id: str
    state: ReviewState
    created_at: str
    updated_at: str
    #: Copied from the result so the queue and the pending list can be ordered and filtered without
    #: reading every formal attempt back.
    percentage: float | None = None
    submitted_at: str | None = None
    auto_submitted: bool = False
    anomaly_count: int = 0
    #: Set when an assessor opens the review. Not a lock — see the module docstring.
    assigned_to: str | None = None
    review_started_at: str | None = None
    decision: AssessorDecisionRecord | None = None
    publish_state: QueuePublishState = QueuePublishState.PENDING
    publish_attempts: int = 0
    published_at: str | None = None
    last_publish_error: str | None = None
    last_publish_attempt_at: str | None = None
    version: int = 1

    @property
    def decided(self) -> bool:
        return self.state in DECIDED_REVIEW_STATES

    @property
    def approved(self) -> bool:
        return self.state is ReviewState.APPROVED

    @property
    def awaiting_publish(self) -> bool:
        return self.publish_state is not QueuePublishState.PUBLISHED

    def start(self, *, assessor_id: str, now: str) -> FormalReview:
        """Record that an assessor has opened the review (§10).

        Re-opening by the same assessor is a no-op change; opening one that is already decided is
        refused, because there is nothing left to review.
        """
        if self.decided:
            raise ReviewAlreadyDecidedError(
                review_id=self.review_id,
                state=self.state.value,
                decided_by=self.decision.decided_by if self.decision else None,
            )
        return replace(
            self,
            state=ReviewState.IN_REVIEW,
            assigned_to=assessor_id,
            review_started_at=self.review_started_at or now,
            updated_at=now,
            version=self.version + 1,
        )

    def decide(
        self, *, decision: AssessorDecision, assessor_id: str, now: str, notes: str | None = None
    ) -> FormalReview:
        """Record the decision (§10). Refuses if one already exists (§20)."""
        if self.decided:
            raise ReviewAlreadyDecidedError(
                review_id=self.review_id,
                state=self.state.value,
                decided_by=self.decision.decided_by if self.decision else None,
            )
        target = (
            ReviewState.APPROVED
            if decision is AssessorDecision.APPROVED
            else ReviewState.REQUIRES_FURTHER_REVIEW
        )
        trimmed = (notes or "").strip()[:MAX_DECISION_NOTES_LENGTH] or None
        return replace(
            self,
            state=target,
            assigned_to=self.assigned_to or assessor_id,
            decision=AssessorDecisionRecord(
                decision=decision, decided_by=assessor_id, decided_at=now, notes=trimmed
            ),
            updated_at=now,
            version=self.version + 1,
        )

    def published(self, *, now: str) -> FormalReview:
        """The queue accepted the notification."""
        return replace(
            self,
            publish_state=QueuePublishState.PUBLISHED,
            publish_attempts=self.publish_attempts + 1,
            published_at=now,
            last_publish_attempt_at=now,
            last_publish_error=None,
            updated_at=now,
            version=self.version + 1,
        )

    def publish_failed(self, *, now: str, error: str, max_attempts: int) -> FormalReview:
        """The queue refused or could not be reached (§13).

        The attempt count decides only whether the *automatic* sweep keeps trying. Either way the
        record stays, the review stays visible and actionable, and the certificate stays blocked.
        """
        attempts = self.publish_attempts + 1
        state = (
            QueuePublishState.FAILED if attempts >= max_attempts else QueuePublishState.PENDING
        )
        return replace(
            self,
            publish_state=state,
            publish_attempts=attempts,
            last_publish_attempt_at=now,
            last_publish_error=error[:500],
            updated_at=now,
            version=self.version + 1,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "formal_attempt_id": self.formal_attempt_id,
            "learner_id": self.learner_id,
            "course_id": self.course_id,
            "quiz_id": self.quiz_id,
            "attempt_id": self.attempt_id,
            "state": self.state.value,
            "percentage": self.percentage,
            "submitted_at": self.submitted_at,
            "auto_submitted": self.auto_submitted,
            "anomaly_count": self.anomaly_count,
            "assigned_to": self.assigned_to,
            "review_started_at": self.review_started_at,
            "decision": self.decision.as_dict() if self.decision else None,
            "queue": {
                "publish_state": self.publish_state.value,
                "publish_attempts": self.publish_attempts,
                "published_at": self.published_at,
                "last_publish_error": self.last_publish_error,
                "last_publish_attempt_at": self.last_publish_attempt_at,
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }


def new_formal_review(
    *,
    review_id: str,
    formal_attempt_id: str,
    learner_id: str,
    course_id: str,
    quiz_id: str,
    attempt_id: str,
    now: str,
    percentage: float | None = None,
    submitted_at: str | None = None,
    auto_submitted: bool = False,
    anomaly_count: int = 0,
) -> FormalReview:
    """A review in PENDING_REVIEW with nothing published yet — the durable state §13 requires."""
    return FormalReview(
        review_id=review_id,
        formal_attempt_id=formal_attempt_id,
        learner_id=learner_id,
        course_id=course_id,
        quiz_id=quiz_id,
        attempt_id=attempt_id,
        state=ReviewState.PENDING_REVIEW,
        created_at=now,
        updated_at=now,
        percentage=percentage,
        submitted_at=submitted_at,
        auto_submitted=auto_submitted,
        anomaly_count=anomaly_count,
        publish_state=QueuePublishState.PENDING,
    )
