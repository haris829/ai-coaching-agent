"""Analytics output contracts (spec section 20).

Two rules govern every model here:

**Nullable metrics, never fabricated ones.** A metric is ``None`` when its
denominator is empty. ``average_score = None`` means "no attempt in scope
carried a score"; ``average_score = 0.0`` means "attempts were scored, and the
mean really is zero". Alongside each metric the count it was computed over is
reported, so a consumer can always see the basis of the number.

**Every response is timestamped.** ``calculated_at`` records when the figures
were computed, so a dashboard can show data freshness (spec section 14).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.analytics.domain.enums import (
    AnalyticsScope,
    DataState,
    FlagReason,
    FlagStatus,
    QuestionSortField,
    ReportingQuestionType,
    ReviewActionType,
    SortDirection,
)
from app.modules.analytics.domain.filters import AnalyticsFilters

__all__ = [
    "OverallAnalytics",
    "QuestionAnalytics",
    "WrongAnswerSummary",
    "QuestionFlagSummary",
    "QuestionAnalyticsResponse",
    "QuestionAnalyticsPage",
    "FlaggedQuestionsResponse",
    "FlagEvaluationResult",
    "PageMeta",
]


class _Output(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OverallAnalytics(_Output):
    """Dashboard metrics for a course or for the whole platform (spec 6)."""

    scope: AnalyticsScope = Field(
        description="COURSE when a course filter was applied, else PLATFORM."
    )
    course_id: str | None = None
    data_state: DataState = Field(
        description="NO_ATTEMPTS when nothing matched the filters; metrics are then null."
    )

    # ------------------------------------------------------------ headline metrics
    average_score: float | None = Field(
        default=None,
        description=(
            "Mean score percentage across scored attempts. Null when no attempt was scored."
        ),
    )
    pass_rate: float | None = Field(
        default=None,
        description=(
            "Percentage of graded attempts that passed. Null when no attempt has a pass/fail "
            "outcome."
        ),
    )
    completion_rate: float | None = Field(
        default=None,
        description=(
            "Percentage of attempts in scope with status COMPLETED. Null when there are no "
            "attempts."
        ),
    )
    attempt_volume: int = Field(ge=0, description="Attempts matching the filters.")

    # ------------------------------------------------------------ metric denominators
    completed_attempts: int = Field(ge=0)
    scored_attempts: int = Field(
        ge=0, description="Denominator behind average_score."
    )
    graded_attempts: int = Field(
        ge=0, description="Denominator behind pass_rate (attempts with a pass/fail outcome)."
    )
    passed_attempts: int = Field(ge=0)
    failed_attempts: int = Field(ge=0)
    unique_learners: int = Field(
        ge=0,
        description=(
            "Distinct learners behind these attempts. Aggregate only; no identities exposed."
        ),
    )

    # ------------------------------------------------------------------ metadata
    filters: AnalyticsFilters
    calculated_at: datetime = Field(description="When these figures were computed (UTC).")
    notes: tuple[str, ...] = Field(
        default=(),
        description="Data-quality remarks, e.g. attempts excluded from a metric for lack of data.",
    )

    @property
    def has_data(self) -> bool:
        return self.data_state is DataState.OK


class WrongAnswerSummary(_Output):
    """The most frequently chosen incorrect answer for a question.

    ``answer`` is a learner-selected option, not the question's answer key.
    """

    answer: str
    count: int = Field(ge=1)
    share_of_incorrect: float = Field(
        ge=0.0,
        le=100.0,
        description=(
            "Percentage of this question's incorrect responses that chose this answer. "
            "The denominator counts incorrect responses that recorded an answer, since "
            "an unanswered response cannot favour any option."
        ),
    )
    tied: bool = Field(
        default=False,
        description=(
            "True when another wrong answer occurred equally often; the lowest-sorting answer "
            "is reported."
        ),
    )


class QuestionFlagSummary(_Output):
    """Persisted flag state for a question, as stored in the review store."""

    status: FlagStatus
    reason: FlagReason
    wrong_answer_rate_at_flag: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Null when the record was not raised by measurement.",
    )
    threshold_used: float | None = Field(default=None, gt=0.0, le=100.0)
    graded_responses_at_flag: int | None = Field(default=None, ge=0)
    flagged_at: datetime
    flagged_by: str
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_action: ReviewActionType | None = None

    @property
    def is_active(self) -> bool:
        return self.status is FlagStatus.FLAGGED


class QuestionAnalytics(_Output):
    """Per-question analytics (spec section 7)."""

    question_id: str
    question_type: ReportingQuestionType = Field(
        description="Normalised type; unknown provider types map to OTHER."
    )
    question_type_label: str = Field(description="Type as it should appear in a report.")

    attempt_count: int = Field(
        ge=0, description="Responses recorded for this question within the filtered scope."
    )
    answered_count: int = Field(ge=0, description="Responses that carried an answer.")
    unanswered_count: int = Field(ge=0, description="Responses with no answer (skipped).")
    graded_count: int = Field(
        ge=0, description="Responses with a grading outcome; denominator of accuracy."
    )
    correct_count: int = Field(ge=0)
    incorrect_count: int = Field(ge=0)

    accuracy_percentage: float | None = Field(
        default=None,
        description="correct_count / graded_count as a percentage. Null when nothing was graded.",
    )
    wrong_answer_rate: float | None = Field(
        default=None,
        description="incorrect_count / graded_count as a percentage. Null when nothing was graded.",
    )
    most_frequent_wrong_answer: WrongAnswerSummary | None = Field(
        default=None, description="Null when there are no incorrect answers to summarise."
    )
    average_time_seconds: float | None = Field(
        default=None,
        description=(
            "Mean time over responses that carried timing data. Null when no timing was "
            "captured."
        ),
    )
    timed_response_count: int = Field(
        ge=0, description="Denominator behind average_time_seconds."
    )

    data_state: DataState = Field(
        description="NO_ATTEMPTS when this question has no responses in scope."
    )

    # ------------------------------------------------------------------- flagging
    flag: QuestionFlagSummary | None = Field(
        default=None, description="Persisted flag record, when one exists."
    )
    is_flagged: bool = Field(
        default=False, description="True when a persisted flag is currently active."
    )
    meets_flag_criteria: bool = Field(
        default=False,
        description=(
            "True when current performance exceeds the configured threshold with "
            "enough responses to be reliable. Independent of whether a flag is "
            "persisted, so read-only analytics can surface candidates without writing."
        ),
    )
    flag_threshold: float = Field(
        gt=0.0, le=100.0, description="Threshold in force when this analysis ran."
    )


class PageMeta(_Output):
    """Pagination metadata for list responses."""

    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    returned: int = Field(ge=0)
    total: int = Field(ge=0)
    sort_by: QuestionSortField
    direction: SortDirection

    @property
    def has_more(self) -> bool:
        return self.offset + self.returned < self.total


class QuestionAnalyticsResponse(_Output):
    """Single-question analytics envelope."""

    question: QuestionAnalytics
    filters: AnalyticsFilters
    calculated_at: datetime


class QuestionAnalyticsPage(_Output):
    """Paged question analytics envelope."""

    items: tuple[QuestionAnalytics, ...] = ()
    page: PageMeta
    data_state: DataState
    filters: AnalyticsFilters
    calculated_at: datetime


class FlaggedQuestionsResponse(_Output):
    """Content-review queue."""

    items: tuple[QuestionAnalytics, ...] = ()
    total: int = Field(ge=0)
    threshold_used: float = Field(gt=0.0, le=100.0)
    min_responses_required: int = Field(ge=1)
    includes_unpersisted_candidates: bool = Field(
        description="True when questions meeting the threshold but not yet persisted are included."
    )
    filters: AnalyticsFilters
    calculated_at: datetime


class FlagEvaluationResult(_Output):
    """Outcome of an explicit flag-evaluation run.

    This is the only analytics operation that writes, and it writes solely to the
    review store: assessment data is never touched (spec section 17).
    """

    evaluated_questions: int = Field(ge=0)
    newly_flagged: tuple[str, ...] = ()
    re_flagged: tuple[str, ...] = Field(
        default=(), description="Previously resolved questions flagged again on fresh evidence."
    )
    already_flagged: tuple[str, ...] = Field(
        default=(), description="Active flags left untouched (flags are never re-raised or reset)."
    )
    below_threshold_retained: tuple[str, ...] = Field(
        default=(),
        description=(
            "Questions whose current rate is below the threshold but whose existing "
            "flag was retained: only a review action may clear a flag."
        ),
    )
    skipped_insufficient_data: tuple[str, ...] = ()
    skipped_retired: tuple[str, ...] = ()
    threshold_used: float = Field(gt=0.0, le=100.0)
    min_responses_required: int = Field(ge=1)
    filters: AnalyticsFilters
    calculated_at: datetime
