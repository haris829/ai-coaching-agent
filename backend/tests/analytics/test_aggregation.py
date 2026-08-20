"""Unit tests for the calculation primitives (spec section 25).

These run without a repository: the arithmetic is pure, so its edge cases are
tested directly rather than through four layers of orchestration.
"""

from __future__ import annotations

import pytest

from app.modules.analytics.domain.enums import AnalyticsScope, DataState, ReportingQuestionType
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.services.aggregation import (
    OverallAccumulator,
    QuestionAccumulator,
    round_metric,
    safe_mean,
    safe_percentage,
)

from .factories import NOW, make_attempt, make_question, make_response


class TestSafeArithmetic:
    def test_percentage_of_empty_denominator_is_none_not_zero(self):
        assert safe_percentage(0, 0) is None
        assert safe_percentage(5, 0) is None

    def test_genuine_zero_is_reported_as_zero(self):
        assert safe_percentage(0, 10) == 0.0

    def test_percentage_computes_normally(self):
        assert safe_percentage(1, 4) == 25.0
        assert safe_percentage(3, 3) == 100.0

    def test_mean_of_no_values_is_none(self):
        assert safe_mean(0.0, 0) is None
        assert safe_mean(100.0, 0) is None

    def test_mean_computes_normally(self):
        assert safe_mean(190.0, 3) == pytest.approx(63.3333, abs=1e-4)

    @pytest.mark.parametrize(
        ("value", "places", "expected"),
        [
            (66.665, 2, 66.67),  # half-up, not banker's rounding
            (2.5, 0, 3.0),
            (33.333333, 2, 33.33),
            (100.0, 2, 100.0),
            (0.0, 2, 0.0),
        ],
    )
    def test_rounding_is_half_up_and_deterministic(self, value, places, expected):
        assert round_metric(value, places) == expected

    def test_rounding_preserves_none(self):
        assert round_metric(None, 2) is None

    def test_rounding_rejects_nan_and_infinity(self):
        assert round_metric(float("nan"), 2) is None
        assert round_metric(float("inf"), 2) is None


class TestOverallAccumulator:
    def test_each_metric_uses_its_own_denominator(self):
        accumulator = OverallAccumulator()
        accumulator.add(make_attempt("a1", learner_id="l1", score=90.0, passed=True))
        accumulator.add(make_attempt("a2", learner_id="l1", score=40.0, passed=False))
        accumulator.add(
            make_attempt("a3", learner_id="l2", status="IN_PROGRESS", score=None, passed=None)
        )

        result = accumulator.build(
            filters=AnalyticsFilters(), calculated_at=NOW, decimal_places=2
        )

        assert result.attempt_volume == 3
        assert result.completed_attempts == 2
        assert result.completion_rate == pytest.approx(66.67)
        assert result.scored_attempts == 2
        assert result.average_score == 65.0  # in-progress attempt excluded
        assert result.graded_attempts == 2
        assert result.pass_rate == 50.0
        assert result.failed_attempts == 1
        assert result.unique_learners == 2

    def test_empty_accumulator_reports_no_attempts_with_null_metrics(self):
        result = OverallAccumulator().build(
            filters=AnalyticsFilters(), calculated_at=NOW, decimal_places=2
        )

        assert result.data_state is DataState.NO_ATTEMPTS
        assert result.average_score is None
        assert result.pass_rate is None
        assert result.completion_rate is None
        assert result.attempt_volume == 0

    def test_attempts_with_no_completion_report_zero_completion_not_null(self):
        accumulator = OverallAccumulator()
        accumulator.add(make_attempt("a1", status="ABANDONED", score=None, passed=None))

        result = accumulator.build(
            filters=AnalyticsFilters(), calculated_at=NOW, decimal_places=2
        )

        assert result.data_state is DataState.OK
        assert result.completion_rate == 0.0  # real zero: there was an attempt
        assert result.average_score is None  # no basis: nothing was scored
        assert result.pass_rate is None

    def test_notes_explain_excluded_attempts(self):
        accumulator = OverallAccumulator()
        accumulator.add(make_attempt("a1", score=90.0, passed=True))
        accumulator.add(make_attempt("a2", status="IN_PROGRESS", score=None, passed=None))

        notes = accumulator.build(
            filters=AnalyticsFilters(), calculated_at=NOW, decimal_places=2
        ).notes

        assert any("average_score" in note for note in notes)
        assert any("pass_rate" in note for note in notes)

    def test_scope_follows_the_course_filter(self):
        accumulator = OverallAccumulator()
        accumulator.add(make_attempt("a1"))

        platform = accumulator.build(
            filters=AnalyticsFilters(), calculated_at=NOW, decimal_places=2
        )
        course = accumulator.build(
            filters=AnalyticsFilters(course_id="course-1"), calculated_at=NOW, decimal_places=2
        )

        assert platform.scope is AnalyticsScope.PLATFORM
        assert platform.course_id is None
        assert course.scope is AnalyticsScope.COURSE
        assert course.course_id == "course-1"

    def test_calculated_at_is_carried_through(self):
        result = OverallAccumulator().build(
            filters=AnalyticsFilters(), calculated_at=NOW, decimal_places=2
        )
        assert result.calculated_at == NOW


class TestQuestionAccumulator:
    def _accumulator(self) -> QuestionAccumulator:
        accumulator = QuestionAccumulator(question_id="question-1")
        for response in [
            make_response("r1", selected_answer="A", is_correct=True, time_spent_seconds=10.0),
            make_response("r2", selected_answer="B", is_correct=False, time_spent_seconds=20.0),
            make_response("r3", selected_answer="B", is_correct=False, time_spent_seconds=None),
            make_response("r4", selected_answer="C", is_correct=False, time_spent_seconds=30.0),
            make_response("r5", selected_answer=None, is_correct=None, time_spent_seconds=None),
        ]:
            accumulator.add(response)
        return accumulator

    def test_counts_separate_answered_graded_and_timed_responses(self):
        accumulator = self._accumulator()

        assert accumulator.attempt_count == 5
        assert accumulator.answered_count == 4
        assert accumulator.unanswered_count == 1
        assert accumulator.graded_count == 4
        assert accumulator.correct_count == 1
        assert accumulator.incorrect_count == 3
        assert accumulator.timed_response_count == 3

    def test_accuracy_and_wrong_rate_use_graded_responses(self):
        accumulator = self._accumulator()

        assert accumulator.accuracy_percentage == 25.0
        assert accumulator.wrong_answer_rate == 75.0

    def test_average_time_ignores_responses_without_timing(self):
        accumulator = self._accumulator()

        # (10 + 20 + 30) / 3, not / 5: missing timing is not zero seconds.
        assert accumulator.average_time_seconds == 20.0

    def test_most_frequent_wrong_answer(self):
        summary = self._accumulator().most_frequent_wrong_answer(2)

        assert summary is not None
        assert summary.answer == "B"
        assert summary.count == 2
        assert summary.share_of_incorrect == pytest.approx(66.67)
        assert summary.tied is False

    def test_ties_are_broken_deterministically_and_flagged(self):
        accumulator = QuestionAccumulator(question_id="q")
        accumulator.add(make_response("r1", selected_answer="Z", is_correct=False))
        accumulator.add(make_response("r2", selected_answer="A", is_correct=False))

        summary = accumulator.most_frequent_wrong_answer(2)

        assert summary is not None
        assert summary.answer == "A"  # lowest-sorting of the tied answers
        assert summary.tied is True

    def test_unanswered_incorrect_responses_do_not_pollute_the_wrong_answer_tally(self):
        accumulator = QuestionAccumulator(question_id="q")
        accumulator.add(make_response("r1", selected_answer=None, is_correct=False))

        assert accumulator.incorrect_count == 1
        assert accumulator.most_frequent_wrong_answer(2) is None

    def test_question_with_no_responses_reports_no_attempts(self):
        result = QuestionAccumulator(question_id="q").build(
            metadata=None, flag=None, threshold=40.0, min_responses=1, decimal_places=2
        )

        assert result.data_state is DataState.NO_ATTEMPTS
        assert result.attempt_count == 0
        assert result.accuracy_percentage is None
        assert result.wrong_answer_rate is None
        assert result.average_time_seconds is None
        assert result.most_frequent_wrong_answer is None
        assert result.meets_flag_criteria is False

    def test_ungraded_responses_leave_accuracy_undefined(self):
        accumulator = QuestionAccumulator(question_id="q")
        accumulator.add(make_response("r1", selected_answer="A", is_correct=None))

        result = accumulator.build(
            metadata=None, flag=None, threshold=40.0, min_responses=1, decimal_places=2
        )

        assert result.attempt_count == 1
        assert result.data_state is DataState.OK
        assert result.graded_count == 0
        assert result.accuracy_percentage is None

    def test_metadata_supplies_the_question_type(self):
        result = QuestionAccumulator(question_id="q").build(
            metadata=make_question("q", question_type="TRUE_FALSE"),
            flag=None,
            threshold=40.0,
            min_responses=1,
            decimal_places=2,
        )

        assert result.question_type is ReportingQuestionType.TRUE_FALSE
        assert result.question_type_label == "TRUE_FALSE"

    def test_missing_metadata_falls_back_to_other(self):
        result = QuestionAccumulator(question_id="q").build(
            metadata=None, flag=None, threshold=40.0, min_responses=1, decimal_places=2
        )

        assert result.question_type is ReportingQuestionType.OTHER

    def test_unknown_provider_type_is_reported_under_its_own_label(self):
        result = QuestionAccumulator(question_id="q").build(
            metadata=make_question("q", question_type="drag-and-drop"),
            flag=None,
            threshold=40.0,
            min_responses=1,
            decimal_places=2,
        )

        assert result.question_type is ReportingQuestionType.OTHER
        assert result.question_type_label == "drag-and-drop"


class TestFlagCriteria:
    @pytest.mark.parametrize(
        ("wrong", "total", "threshold", "min_responses", "expected"),
        [
            (3, 4, 40.0, 3, True),  # 75% over a 40% threshold
            (2, 5, 40.0, 3, False),  # 40% is not *above* 40%
            (3, 5, 40.0, 3, True),  # 60%
            (1, 1, 40.0, 3, False),  # 100% but too few responses
            (0, 5, 40.0, 3, False),
        ],
    )
    def test_threshold_and_sample_size_both_gate_flagging(
        self, wrong, total, threshold, min_responses, expected
    ):
        accumulator = QuestionAccumulator(question_id="q")
        for index in range(total):
            accumulator.add(
                make_response(
                    f"r{index}",
                    selected_answer="B" if index < wrong else "A",
                    is_correct=index >= wrong,
                )
            )

        assert (
            accumulator.meets_flag_criteria(threshold=threshold, min_responses=min_responses)
            is expected
        )

    def test_threshold_is_not_hardcoded_in_the_calculation(self):
        accumulator = QuestionAccumulator(question_id="q")
        for index in range(10):
            accumulator.add(
                make_response(f"r{index}", selected_answer="B", is_correct=index >= 3)
            )

        # 30% wrong: flagged under a 25% threshold, not under a 35% one.
        assert accumulator.meets_flag_criteria(threshold=25.0, min_responses=3) is True
        assert accumulator.meets_flag_criteria(threshold=35.0, min_responses=3) is False
