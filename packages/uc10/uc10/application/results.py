"""Outcome types.

Rating capture returns a *result*, never an exception, for every foreseeable failure.
That is what makes "a feedback write failure cannot propagate into the caller's main
path" a structural property rather than a promise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from uc10.domain.flagging import FlagDecision
from uc10.domain.models import ContentReviewFlag, RatingRecord


class RatingCaptureStatus(StrEnum):
    RECORDED = "recorded"
    REPLACED = "replaced"
    REJECTED_ANONYMOUS = "rejected_anonymous"
    REJECTED_NOT_FOUND = "rejected_not_found"
    REJECTED_WINDOW_EXPIRED = "rejected_window_expired"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_PERMANENT = "failed_permanent"

    @property
    def is_success(self) -> bool:
        return self in (RatingCaptureStatus.RECORDED, RatingCaptureStatus.REPLACED)


#: Learner-facing messages. Deliberately content-free: no rating, comment or response text.
CAPTURE_MESSAGES: dict[RatingCaptureStatus, str] = {
    RatingCaptureStatus.RECORDED: "Thanks -- your feedback was saved.",
    RatingCaptureStatus.REPLACED: "Thanks -- your feedback was updated.",
    RatingCaptureStatus.REJECTED_ANONYMOUS: "Sign in to leave feedback.",
    RatingCaptureStatus.REJECTED_NOT_FOUND: "That response could not be found.",
    RatingCaptureStatus.REJECTED_WINDOW_EXPIRED: (
        "Feedback closed for this response. Responses can be rated for a limited period "
        "after they are delivered."
    ),
    RatingCaptureStatus.FAILED_RETRYABLE: "Your feedback could not be saved. Please try again.",
    RatingCaptureStatus.FAILED_PERMANENT: "Your feedback could not be saved.",
}


@dataclass(frozen=True, slots=True)
class RatingCaptureResult:
    status: RatingCaptureStatus
    message: str
    retryable: bool = False
    rating: RatingRecord | None = None
    superseded_rating_id: str | None = None
    reason_code: str | None = None

    @classmethod
    def of(
        cls,
        status: RatingCaptureStatus,
        *,
        rating: RatingRecord | None = None,
        superseded_rating_id: str | None = None,
        reason_code: str | None = None,
    ) -> RatingCaptureResult:
        return cls(
            status=status,
            message=CAPTURE_MESSAGES[status],
            retryable=status is RatingCaptureStatus.FAILED_RETRYABLE,
            rating=rating,
            superseded_rating_id=superseded_rating_id,
            reason_code=reason_code,
        )

    @property
    def ok(self) -> bool:
        return self.status.is_success


class FlagWriteStatus(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    NOT_REQUIRED = "not_required"
    DEFERRED = "deferred"  # write failed; intent retained for the next cycle


@dataclass(frozen=True, slots=True)
class FlagEvaluationResult:
    topic_tag: str
    decision: FlagDecision
    write_status: FlagWriteStatus
    flag: ContentReviewFlag | None = None
    work_id: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class CycleReport:
    """What one evaluation cycle did. Counts and identifiers only."""

    evaluated_topics: tuple[str, ...] = ()
    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    retried: tuple[str, ...] = ()
    results: list[FlagEvaluationResult] = field(default_factory=list)

    @property
    def pending_after(self) -> int:
        return len(self.deferred)
