"""Pure aggregation primitives - the single calculation layer.

Everything numeric in UC-10 is computed here. The API responses and the CSV
exports both reach these same accumulators through
:class:`~app.modules.analytics.services.analytics_service.AnalyticsService`, so an
export can never disagree with the dashboard (spec section 10).

Three properties are deliberate:

**Single pass, bounded state.** Accumulators consume one record at a time and
keep only running totals, so aggregation cost is linear in records and memory
does not grow with the dataset. The one exception is documented on
:attr:`OverallAccumulator.unique_learners`.

**No division by zero, ever.** Every ratio goes through
:func:`safe_percentage`, which returns ``None`` for an empty denominator rather
than raising or inventing a zero (spec sections 7 and 12).

**Deterministic output.** Rounding is half-up via :class:`~decimal.Decimal`, and
tie-breaks (most frequent wrong answer, sort order) are resolved by explicit
rules rather than by dict ordering, so the same input always produces
byte-identical output.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.modules.analytics.domain.analytics import (
    OverallAnalytics,
    QuestionAnalytics,
    QuestionFlagSummary,
    WrongAnswerSummary,
)
from app.modules.analytics.domain.enums import AnalyticsScope, DataState, ReportingQuestionType
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.domain.records import (
    AttemptRecord,
    QuestionFlagRecord,
    QuestionMetadata,
    ResponseRecord,
)

__all__ = [
    "safe_percentage",
    "safe_mean",
    "round_metric",
    "OverallAccumulator",
    "QuestionAccumulator",
    "flag_summary_from_record",
]


# --------------------------------------------------------------------- helpers


def safe_percentage(numerator: int | float, denominator: int | float) -> float | None:
    """``numerator / denominator`` as a percentage, or ``None`` if there is no basis.

    An empty denominator means the metric is undefined, which is materially
    different from a metric that genuinely evaluates to zero. Returning ``None``
    keeps that distinction visible all the way to the API contract.
    """
    if denominator is None or denominator == 0:
        return None
    return (numerator / denominator) * 100.0


def safe_mean(total: float, count: int) -> float | None:
    """Mean of ``count`` values summing to ``total``, or ``None`` when count is 0."""
    if count == 0:
        return None
    return total / count


def round_metric(value: float | None, decimal_places: int) -> float | None:
    """Round half-up to ``decimal_places``, preserving ``None``.

    ``round()`` would apply banker's rounding, so 66.665 would report as 66.66 in
    one place and 66.67 in another depending on the binary representation. Going
    through ``Decimal(str(value))`` makes the reported figure predictable and
    identical in JSON and CSV.
    """
    if value is None:
        return None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / infinity
        return None
    try:
        quantum = Decimal(1).scaleb(-decimal_places)
        return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):  # pragma: no cover - defensive
        return None


def flag_summary_from_record(record: QuestionFlagRecord | None) -> QuestionFlagSummary | None:
    """Project a persisted flag record onto the API contract."""
    if record is None:
        return None
    return QuestionFlagSummary(
        status=record.status,
        reason=record.reason,
        wrong_answer_rate_at_flag=record.wrong_answer_rate,
        threshold_used=record.threshold_used,
        graded_responses_at_flag=record.graded_responses_at_flag,
        flagged_at=record.flagged_at,
        flagged_by=record.flagged_by,
        resolved_at=record.resolved_at,
        resolved_by=record.resolved_by,
        resolution_action=record.resolution_action,
    )


# ------------------------------------------------------------------ dashboards


@dataclass
class OverallAccumulator:
    """Running totals for the dashboard metrics (spec section 6).

    Each metric tracks its own denominator, because attempts legitimately carry
    different amounts of information: an in-progress attempt has no score, and a
    completed attempt may not yet have a pass/fail decision. Mixing those into
    one denominator would quietly understate every metric.
    """

    attempt_volume: int = 0
    completed_attempts: int = 0
    scored_attempts: int = 0
    score_total: float = 0.0
    graded_attempts: int = 0
    passed_attempts: int = 0
    #: Distinct learner ids. This is the only unbounded structure in
    #: aggregation: it grows with distinct learners, not with attempts. At
    #: platform scale, swap for a cardinality estimator behind the same field.
    _learners: set[str] = field(default_factory=set, repr=False)

    def add(self, attempt: AttemptRecord) -> None:
        self.attempt_volume += 1
        self._learners.add(attempt.learner_id)
        if attempt.is_completed:
            self.completed_attempts += 1
        if attempt.score is not None:
            self.scored_attempts += 1
            self.score_total += attempt.score
        if attempt.passed is not None:
            self.graded_attempts += 1
            if attempt.passed:
                self.passed_attempts += 1

    @property
    def unique_learners(self) -> int:
        return len(self._learners)

    @property
    def failed_attempts(self) -> int:
        return self.graded_attempts - self.passed_attempts

    def notes(self) -> tuple[str, ...]:
        """Plain-language remarks about data excluded from each metric."""
        notes: list[str] = []
        unscored = self.attempt_volume - self.scored_attempts
        if unscored > 0:
            notes.append(
                f"{unscored} of {self.attempt_volume} attempts carry no score and are "
                "excluded from average_score."
            )
        ungraded = self.attempt_volume - self.graded_attempts
        if ungraded > 0:
            notes.append(
                f"{ungraded} of {self.attempt_volume} attempts have no pass/fail outcome "
                "and are excluded from pass_rate."
            )
        return tuple(notes)

    def build(
        self,
        *,
        filters: AnalyticsFilters,
        calculated_at: datetime,
        decimal_places: int,
    ) -> OverallAnalytics:
        empty = self.attempt_volume == 0
        return OverallAnalytics(
            scope=AnalyticsScope.COURSE if filters.course_id else AnalyticsScope.PLATFORM,
            course_id=filters.course_id,
            data_state=DataState.NO_ATTEMPTS if empty else DataState.OK,
            average_score=round_metric(
                safe_mean(self.score_total, self.scored_attempts), decimal_places
            ),
            pass_rate=round_metric(
                safe_percentage(self.passed_attempts, self.graded_attempts), decimal_places
            ),
            completion_rate=round_metric(
                safe_percentage(self.completed_attempts, self.attempt_volume), decimal_places
            ),
            attempt_volume=self.attempt_volume,
            completed_attempts=self.completed_attempts,
            scored_attempts=self.scored_attempts,
            graded_attempts=self.graded_attempts,
            passed_attempts=self.passed_attempts,
            failed_attempts=self.failed_attempts,
            unique_learners=self.unique_learners,
            filters=filters,
            calculated_at=calculated_at,
            notes=self.notes(),
        )


# ------------------------------------------------------------------- questions


@dataclass
class QuestionAccumulator:
    """Running totals for one question (spec section 7).

    Counting rules, chosen so that no category silently swallows another:

    * ``attempt_count`` - every response seen for this question.
    * ``answered_count`` / ``unanswered_count`` - whether an answer was recorded.
      A skipped question is still an attempt at it.
    * ``graded_count`` - responses with a grading outcome. This is the denominator
      of accuracy, so an ungraded response can never look like a wrong answer.
    * ``timed_response_count`` - responses carrying timing data, the denominator
      of average time. Missing timing does not count as zero seconds, which would
      drag the average down.
    """

    question_id: str
    attempt_count: int = 0
    answered_count: int = 0
    graded_count: int = 0
    correct_count: int = 0
    incorrect_count: int = 0
    time_total: float = 0.0
    timed_response_count: int = 0
    #: Bounded by the number of distinct answer options, not by response volume.
    _wrong_answers: Counter[str] = field(default_factory=Counter, repr=False)

    def add(self, response: ResponseRecord) -> None:
        self.attempt_count += 1
        if response.selected_answer is not None:
            self.answered_count += 1
        if response.time_spent_seconds is not None:
            self.timed_response_count += 1
            self.time_total += response.time_spent_seconds
        if response.is_correct is None:
            return  # ungraded: counted as an attempt, excluded from accuracy
        self.graded_count += 1
        if response.is_correct:
            self.correct_count += 1
        else:
            self.incorrect_count += 1
            if response.selected_answer is not None:
                self._wrong_answers[response.selected_answer] += 1

    @property
    def unanswered_count(self) -> int:
        return self.attempt_count - self.answered_count

    @property
    def accuracy_percentage(self) -> float | None:
        return safe_percentage(self.correct_count, self.graded_count)

    @property
    def wrong_answer_rate(self) -> float | None:
        return safe_percentage(self.incorrect_count, self.graded_count)

    @property
    def average_time_seconds(self) -> float | None:
        return safe_mean(self.time_total, self.timed_response_count)

    def most_frequent_wrong_answer(self, decimal_places: int) -> WrongAnswerSummary | None:
        """Most chosen incorrect answer.

        Ties are broken by lexicographic order of the answer, and flagged with
        ``tied=True`` so a reviewer knows the choice was arbitrary. Without an
        explicit rule the winner would depend on iteration order and the CSV
        would stop being reproducible.
        """
        if not self._wrong_answers:
            return None
        top_count = max(self._wrong_answers.values())
        contenders = sorted(
            answer for answer, count in self._wrong_answers.items() if count == top_count
        )
        answered_incorrect = sum(self._wrong_answers.values())
        share = safe_percentage(top_count, answered_incorrect) or 0.0
        return WrongAnswerSummary(
            answer=contenders[0],
            count=top_count,
            share_of_incorrect=round_metric(share, decimal_places) or 0.0,
            tied=len(contenders) > 1,
        )

    def meets_flag_criteria(self, *, threshold: float, min_responses: int) -> bool:
        """Whether current performance warrants a content-review flag.

        Requires both a rate strictly above the threshold and enough graded
        responses to trust it, so a single wrong answer on a brand-new question
        cannot put it in the review queue.
        """
        if self.graded_count < min_responses:
            return False
        rate = self.wrong_answer_rate
        return rate is not None and rate > threshold

    def build(
        self,
        *,
        metadata: QuestionMetadata | None,
        flag: QuestionFlagRecord | None,
        threshold: float,
        min_responses: int,
        decimal_places: int,
    ) -> QuestionAnalytics:
        question_type = metadata.question_type if metadata else ReportingQuestionType.OTHER
        type_label = metadata.display_type if metadata else ReportingQuestionType.OTHER.value
        return QuestionAnalytics(
            question_id=self.question_id,
            question_type=question_type,
            question_type_label=type_label,
            attempt_count=self.attempt_count,
            answered_count=self.answered_count,
            unanswered_count=self.unanswered_count,
            graded_count=self.graded_count,
            correct_count=self.correct_count,
            incorrect_count=self.incorrect_count,
            accuracy_percentage=round_metric(self.accuracy_percentage, decimal_places),
            wrong_answer_rate=round_metric(self.wrong_answer_rate, decimal_places),
            most_frequent_wrong_answer=self.most_frequent_wrong_answer(decimal_places),
            average_time_seconds=round_metric(self.average_time_seconds, decimal_places),
            timed_response_count=self.timed_response_count,
            data_state=DataState.NO_ATTEMPTS if self.attempt_count == 0 else DataState.OK,
            flag=flag_summary_from_record(flag),
            is_flagged=flag is not None and flag.is_active,
            meets_flag_criteria=self.meets_flag_criteria(
                threshold=threshold, min_responses=min_responses
            ),
            flag_threshold=threshold,
        )
