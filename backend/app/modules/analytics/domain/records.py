"""Repository record contracts - the shape of data UC-10 expects to receive.

These models are the integration boundary. The external assessment system does
not need to know about UC-10's internals; it only has to hand over records that
satisfy these contracts (spec sections 2 and 21).

Design rules:

* **Tolerant of incompleteness.** Timing, answers, scores and pass/fail are all
  optional, because real attempt data is incomplete: in-progress attempts have
  no score, skipped questions have no answer, and some providers do not capture
  per-question timing. Aggregation treats each ``None`` as "excluded from that
  metric's denominator" rather than as zero.
* **Strict about meaning.** Scores are percentages on a 0-100 scale, durations
  are non-negative seconds, timestamps are timezone-aware UTC. A record that
  violates these is a data-quality fault and is rejected loudly.
* **No answer keys.** There is deliberately no field for a question's correct
  answer anywhere in this contract; UC-10 never needs one and therefore can
  never leak one (spec section 23).
* **Extra fields ignored.** Providers may send richer payloads; unknown keys are
  dropped instead of failing the aggregation.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.time import ensure_utc
from app.modules.analytics.domain.enums import (
    AssessmentType,
    AttemptStatus,
    FlagReason,
    FlagStatus,
    ReportingQuestionType,
    ReviewActionType,
)

__all__ = [
    "AttemptRecord",
    "ResponseRecord",
    "QuestionMetadata",
    "QuestionFlagRecord",
    "ReviewActionRecord",
    "PageRequest",
    "Page",
]

T = TypeVar("T")


class _ExternalRecord(BaseModel):
    """Base for records supplied by the external provider."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


class AttemptRecord(_ExternalRecord):
    """One learner's attempt at one assessment.

    ``learner_id`` is required for cohort-level correctness and de-duplication
    but is never included in any analytics output; only aggregate counts derived
    from it are reported.
    """

    attempt_id: str = Field(min_length=1)
    course_id: str = Field(min_length=1)
    learner_id: str = Field(min_length=1, description="Never exposed in analytics output.")
    cohort_id: str | None = Field(
        default=None, description="Learner group, when the learner belongs to one."
    )
    assessment_type: AssessmentType
    status: AttemptStatus
    started_at: datetime = Field(description="Filter anchor for date ranges.")
    completed_at: datetime | None = None
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Final score as a percentage. None when the attempt was never scored.",
    )
    passed: bool | None = Field(
        default=None,
        description="Pass/fail outcome. None when the attempt has no pass/fail decision yet.",
    )

    @field_validator("started_at", "completed_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_utc(value)

    @field_validator("cohort_id")
    @classmethod
    def _blank_cohort_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @property
    def is_completed(self) -> bool:
        """Completion is driven by status, not by the presence of a timestamp.

        Some providers record ``completed_at`` on abandoned attempts (the moment
        the session was closed out), so status is the authoritative signal.
        """
        return self.status is AttemptStatus.COMPLETED

    @property
    def is_scored(self) -> bool:
        return self.score is not None

    @property
    def is_graded(self) -> bool:
        return self.passed is not None

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        delta = (self.completed_at - self.started_at).total_seconds()
        return delta if delta >= 0 else None


class ResponseRecord(_ExternalRecord):
    """One learner's response to one question inside an attempt.

    A response with ``selected_answer is None`` is a skipped question; a
    response with ``is_correct is None`` was never graded. Both are counted as
    responses but excluded from accuracy.
    """

    response_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    selected_answer: str | None = Field(
        default=None,
        description="Learner's answer as reported by the provider. None means unanswered.",
    )
    is_correct: bool | None = Field(
        default=None,
        description="Grading outcome. None means ungraded or skipped.",
    )
    time_spent_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="Seconds spent on the question. None when the provider captures no timing.",
    )
    answered_at: datetime | None = Field(
        default=None,
        description="Used to decide whether a resolved flag has fresh evidence.",
    )

    @field_validator("answered_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_utc(value)

    @field_validator("selected_answer")
    @classmethod
    def _blank_answer_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @property
    def is_answered(self) -> bool:
        return self.selected_answer is not None

    @property
    def is_graded(self) -> bool:
        return self.is_correct is not None


class QuestionMetadata(_ExternalRecord):
    """Descriptive information about a question.

    Note the absence of any correct-answer field: see the module docstring.
    """

    question_id: str = Field(min_length=1)
    question_type: ReportingQuestionType = ReportingQuestionType.OTHER
    question_type_label: str | None = Field(
        default=None,
        description=(
            "Provider's original type label, retained when it is not a known "
            "ReportingQuestionType."
        ),
    )
    course_id: str | None = None
    text: str | None = Field(
        default=None,
        description="Question text or a short excerpt, for review context only.",
    )
    active: bool = Field(
        default=True,
        description="False when the source system has already withdrawn the question.",
    )

    @model_validator(mode="before")
    @classmethod
    def _retain_raw_type(cls, data: Any) -> Any:
        """Keep the provider's spelling of an unrecognised question type."""
        if not isinstance(data, Mapping):
            return data
        raw = data.get("question_type")
        if raw is None or data.get("question_type_label") is not None:
            return data
        try:
            known = (
                raw
                if isinstance(raw, ReportingQuestionType)
                else ReportingQuestionType(raw)
            )
        except ValueError:  # pragma: no cover - _missing_ makes this unreachable
            known = ReportingQuestionType.OTHER
        if (
            known is ReportingQuestionType.OTHER
            and str(raw).strip().upper() != ReportingQuestionType.OTHER.value
        ):
            mutable = dict(data)
            mutable["question_type_label"] = str(raw)
            return mutable
        return data

    @property
    def display_type(self) -> str:
        """Type to show in reports: the provider's own name whenever it supplied one.

        UC-10 originally used the provider's label only when normalisation had fallen back to
        ``OTHER`` — sensible when the provider's vocabulary was unknown and its labels might be
        anything. In the merged system the provider *is* this system, its five type names are the
        authoritative ones, and the mapping in ``integration/question_types.py`` is deliberately
        lossy: ``SINGLE_CHOICE`` normalises to ``MULTIPLE_CHOICE`` so analytics can group by a
        generic shape.

        Showing the normalised name would undo that on the way out — an administrator would read
        "MULTIPLE_CHOICE" for a question the authoring screen calls "Single choice". So the label
        wins whenever one is supplied, and the enum value is the fallback for a provider that
        offers none.
        """
        return self.question_type_label or self.question_type.value


class QuestionFlagRecord(_ExternalRecord):
    """Persisted content-review flag.

    Lives in the review store, not in assessment data. Created by threshold
    evaluation, and only ever transitioned by a review action, so recalculating
    analytics cannot erase it (spec section 18).
    """

    question_id: str = Field(min_length=1)
    status: FlagStatus = FlagStatus.FLAGGED
    reason: FlagReason = FlagReason.WRONG_ANSWER_RATE_EXCEEDED
    wrong_answer_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description=(
            "Wrong-answer rate measured when the flag was raised. Null for a record "
            "created by administrative action, where nothing was measured."
        ),
    )
    threshold_used: float | None = Field(
        default=None,
        gt=0.0,
        le=100.0,
        description="Threshold in force when the flag was raised. Null for administrative records.",
    )
    graded_responses_at_flag: int | None = Field(
        default=None,
        ge=0,
        description="Sample size behind the measurement. Null for administrative records.",
    )
    flagged_at: datetime
    flagged_by: str = Field(default="system", min_length=1)
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_action: ReviewActionType | None = None
    updated_at: datetime | None = None

    @field_validator("flagged_at", "resolved_at", "updated_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_utc(value)

    @property
    def is_active(self) -> bool:
        return self.status is FlagStatus.FLAGGED

    @property
    def is_terminal(self) -> bool:
        """Retired questions are never re-flagged."""
        return self.status is FlagStatus.RETIRED


class ReviewActionRecord(_ExternalRecord):
    """Immutable audit entry for an administrator's review decision."""

    action_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    action: ReviewActionType
    admin_id: str = Field(min_length=1, description="Authenticated administrator identity.")
    created_at: datetime
    note: str | None = Field(default=None, max_length=2000)
    previous_flag_status: FlagStatus | None = None
    resulting_flag_status: FlagStatus | None = None

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class PageRequest(BaseModel):
    """Cursor pagination request handed to the repository.

    Cursor-based rather than offset-based so a provider can page over a moving
    dataset without skipping or repeating records.
    """

    model_config = ConfigDict(frozen=True)

    cursor: str | None = Field(default=None, description="Opaque cursor from the previous page.")
    limit: int = Field(default=500, ge=1, le=50_000)


class Page(BaseModel, Generic[T]):
    """One page of repository records."""

    model_config = ConfigDict(frozen=True)

    items: tuple[T, ...] = ()
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the following page. None means this was the last page.",
    )
    total: int | None = Field(
        default=None,
        description="Optional total count, when the provider can supply it cheaply.",
    )

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None

    def __len__(self) -> int:
        return len(self.items)
