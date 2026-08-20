"""Filter tests (spec sections 9, 25).

Covers the three filter dimensions, their combination, the half-open date-range
boundary, and - most importantly - that the same filters produce the same
population in every analytics output, including the CSV exports.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError
from pydantic import ValidationError as PydanticValidationError

from app.modules.analytics.domain.enums import AssessmentType
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.errors import InvalidFilterError

from .factories import BASE_TIME

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio


class TestCourseFilter:
    async def test_restricts_attempts_to_the_course(self, analytics_service, context):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(course_id="course-1"), context
        )
        assert result.attempt_volume == 4

    async def test_restricts_question_responses_to_the_course(
        self, analytics_service, context
    ):
        questions = await analytics_service.aggregate_question_analytics(
            AnalyticsFilters(course_id="course-2"), context
        )

        # Only a4 (course-2) answered question-1; nothing answered question-2.
        assert [q.question_id for q in questions] == ["question-1"]
        assert questions[0].attempt_count == 1


class TestCohortFilter:
    @pytest.mark.parametrize(
        ("cohort", "expected_volume", "expected_learners"),
        [("cohort-a", 3, 2), ("cohort-b", 2, 2)],
    )
    async def test_restricts_to_the_learner_group(
        self, analytics_service, context, cohort, expected_volume, expected_learners
    ):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(cohort_id=cohort), context
        )

        assert result.attempt_volume == expected_volume
        assert result.unique_learners == expected_learners

    async def test_unknown_cohort_yields_the_empty_state(self, analytics_service, context):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(cohort_id="cohort-zz"), context
        )

        assert result.attempt_volume == 0
        assert result.average_score is None

    async def test_cohort_filter_applies_to_question_analytics(
        self, analytics_service, context
    ):
        questions = await analytics_service.aggregate_question_analytics(
            AnalyticsFilters(cohort_id="cohort-b"), context
        )

        # cohort-b covers a3 (ungraded response) and a4 (one wrong answer).
        by_id = {q.question_id: q for q in questions}
        assert by_id["question-1"].attempt_count == 2
        assert by_id["question-1"].graded_count == 1
        assert by_id["question-1"].accuracy_percentage == 0.0


class TestAssessmentTypeFilter:
    async def test_standard_quiz(self, analytics_service, context):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(assessment_type=AssessmentType.STANDARD_QUIZ), context
        )

        assert result.attempt_volume == 3
        assert result.average_score == 65.0

    async def test_formal_assessment(self, analytics_service, context):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(assessment_type=AssessmentType.FORMAL_ASSESSMENT), context
        )

        assert result.attempt_volume == 2
        assert result.average_score == 60.0

    async def test_types_partition_the_population(self, analytics_service, context):
        everything = await analytics_service.get_overall_analytics(AnalyticsFilters(), context)
        quiz = await analytics_service.get_overall_analytics(
            AnalyticsFilters(assessment_type=AssessmentType.STANDARD_QUIZ), context
        )
        formal = await analytics_service.get_overall_analytics(
            AnalyticsFilters(assessment_type=AssessmentType.FORMAL_ASSESSMENT), context
        )

        assert quiz.attempt_volume + formal.attempt_volume == everything.attempt_volume


class TestDateRangeFilter:
    async def test_range_is_half_open(self, analytics_service, context):
        """[start, end): a2 and a3 are in, a4 sits exactly on the exclusive bound."""
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(
                start_date=BASE_TIME + timedelta(days=1),
                end_date=BASE_TIME + timedelta(days=3),
            ),
            context,
        )

        assert result.attempt_volume == 2

    async def test_start_bound_is_inclusive(self, analytics_service, context):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(start_date=BASE_TIME), context
        )

        assert result.attempt_volume == 5

    async def test_open_ended_lower_bound(self, analytics_service, context):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(end_date=BASE_TIME + timedelta(days=1)), context
        )

        assert result.attempt_volume == 1  # a1 only

    async def test_consecutive_periods_tile_without_double_counting(
        self, analytics_service, context
    ):
        boundary = BASE_TIME + timedelta(days=2)
        first = await analytics_service.get_overall_analytics(
            AnalyticsFilters(start_date=BASE_TIME, end_date=boundary), context
        )
        second = await analytics_service.get_overall_analytics(
            AnalyticsFilters(start_date=boundary, end_date=BASE_TIME + timedelta(days=10)),
            context,
        )
        whole = await analytics_service.get_overall_analytics(
            AnalyticsFilters(start_date=BASE_TIME, end_date=BASE_TIME + timedelta(days=10)),
            context,
        )

        assert first.attempt_volume + second.attempt_volume == whole.attempt_volume

    async def test_a_naive_datetime_is_refused_rather_than_assumed_to_be_utc(self):
        """UC-10 coerced a naive datetime to UTC; the merged system refuses it.

        The stricter behaviour is the merged one's throughout — ``app.db.types.UtcDateTime`` refuses
        naive values too — and it is the right way round for a reporting filter. Silently assuming a
        timezone is how a January report quietly includes or excludes several hours of December, and
        nobody notices because the number still looks plausible.

        Nothing legitimate is lost: the API layer parses ISO-8601 strings, which carry an offset.
        """
        with pytest.raises(PydanticValidationError):
            AnalyticsFilters(start_date=BASE_TIME.replace(tzinfo=None))

        with pytest.raises(PydanticValidationError):
            AnalyticsFilters(end_date=BASE_TIME.replace(tzinfo=None))

    async def test_date_filter_applies_to_question_analytics(
        self, analytics_service, context
    ):
        questions = await analytics_service.aggregate_question_analytics(
            AnalyticsFilters(end_date=BASE_TIME + timedelta(days=1)), context
        )

        by_id = {q.question_id: q for q in questions}
        assert by_id["question-1"].attempt_count == 1  # r1 from a1
        assert by_id["question-2"].attempt_count == 1  # r6 from a1


class TestCombinedFilters:
    async def test_filters_intersect(self, analytics_service, context):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(
                course_id="course-1",
                cohort_id="cohort-a",
                assessment_type=AssessmentType.STANDARD_QUIZ,
                start_date=BASE_TIME,
                end_date=BASE_TIME + timedelta(days=2),
            ),
            context,
        )

        assert result.attempt_volume == 2  # a1, a2
        assert result.average_score == 65.0

    async def test_contradictory_filters_yield_the_empty_state(
        self, analytics_service, context
    ):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(course_id="course-2", cohort_id="cohort-a"), context
        )

        assert result.attempt_volume == 0
        assert result.average_score is None


class TestFilterValidation:
    def test_start_after_end_is_rejected(self):
        with pytest.raises(InvalidFilterError) as exc:
            AnalyticsFilters(
                start_date=BASE_TIME + timedelta(days=1), end_date=BASE_TIME
            )

        assert exc.value.code == "INVALID_FILTER"
        assert exc.value.http_status == 422

    def test_identical_bounds_are_rejected_as_an_empty_window(self):
        with pytest.raises(InvalidFilterError):
            AnalyticsFilters(start_date=BASE_TIME, end_date=BASE_TIME)

    def test_blank_identifiers_are_rejected(self):
        with pytest.raises(ValidationError):
            AnalyticsFilters(course_id="   ")

    def test_unknown_filter_field_is_rejected(self):
        with pytest.raises(ValidationError):
            AnalyticsFilters(learner_id="l1")  # analytics never filters by learner

    def test_filters_are_immutable(self):
        filters = AnalyticsFilters(course_id="course-1")

        with pytest.raises(ValidationError):
            filters.course_id = "course-2"

    def test_scope_derives_from_the_course_filter(self):
        assert AnalyticsFilters().scope.value == "PLATFORM"
        assert AnalyticsFilters(course_id="c").scope.value == "COURSE"

    def test_applied_fields_lists_only_supplied_filters(self):
        filters = AnalyticsFilters(course_id="c", start_date=BASE_TIME)

        assert set(filters.applied_fields()) == {"course_id", "start_date"}

    def test_describe_is_log_safe(self):
        described = AnalyticsFilters(course_id="c", cohort_id="h").describe()

        assert "learner_id" not in described
        assert described["scope"] == "COURSE"


class TestFilterConsistencyAcrossOutputs:
    async def test_overall_questions_and_csv_agree_on_the_population(
        self, analytics_service, export_service, context
    ):
        filters = AnalyticsFilters(course_id="course-1", cohort_id="cohort-a")

        overall = await analytics_service.get_overall_analytics(filters, context)
        questions = await analytics_service.aggregate_question_analytics(filters, context)
        csv_export = await export_service.export_questions(filters, context)

        assert overall.attempt_volume == 3
        # Every response counted at question level belongs to an attempt in scope.
        assert sum(q.attempt_count for q in questions) == 5
        assert csv_export.row_count == len(questions)

    async def test_csv_and_api_report_identical_figures_under_the_same_filters(
        self, analytics_service, export_service, context
    ):
        filters = AnalyticsFilters(assessment_type=AssessmentType.STANDARD_QUIZ)

        api = await analytics_service.get_overall_analytics(filters, context)
        exported = (await export_service.export_overall(filters, context)).render()

        assert f"{api.average_score:.2f}" in exported
        assert f"{api.attempt_volume}" in exported
        assert api.filters.assessment_type.value in exported

    async def test_flagged_queue_respects_filters(
        self, analytics_service, review_store, context
    ):
        from .factories import make_flag

        await review_store.put_flag(make_flag("question-1"))

        in_scope = await analytics_service.get_flagged_questions(
            AnalyticsFilters(course_id="course-1"), context
        )

        assert [q.question_id for q in in_scope.items] == ["question-1"]
        assert in_scope.items[0].attempt_count == 4  # course-1 responses only
