"""Question-level analytics tests (spec sections 7, 25)."""

from __future__ import annotations

import pytest

from app.modules.analytics.domain.enums import (
    DataState,
    QuestionSortField,
    ReportingQuestionType,
    SortDirection,
)
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.errors import NotFoundError
from app.modules.analytics.repositories.in_memory import InMemoryAnalyticsRepository
from app.modules.analytics.services import AnalyticsService

from .conftest import make_settings
from .factories import NOW, make_attempt, make_flag, make_question, make_response

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio


async def question(service, context, question_id, filters=None):
    questions = await service.aggregate_question_analytics(filters or AnalyticsFilters(), context)
    matches = [q for q in questions if q.question_id == question_id]
    return matches[0] if matches else None


class TestQuestionMetrics:
    async def test_all_required_fields_are_reported(self, analytics_service, context):
        result = await question(analytics_service, context, "question-1")

        assert result.question_id == "question-1"
        assert result.question_type is ReportingQuestionType.MULTIPLE_CHOICE
        assert result.attempt_count == 5
        assert result.correct_count == 1
        assert result.incorrect_count == 3
        assert result.accuracy_percentage == 25.0
        assert result.most_frequent_wrong_answer.answer == "B"
        assert result.average_time_seconds == 20.0

    async def test_counts_distinguish_answered_graded_and_timed(
        self, analytics_service, context
    ):
        result = await question(analytics_service, context, "question-1")

        assert result.answered_count == 4
        assert result.unanswered_count == 1
        assert result.graded_count == 4
        assert result.timed_response_count == 3

    async def test_wrong_answer_rate_complements_accuracy(self, analytics_service, context):
        result = await question(analytics_service, context, "question-1")

        assert result.wrong_answer_rate == 75.0
        assert result.accuracy_percentage + result.wrong_answer_rate == 100.0

    async def test_question_answered_correctly_by_everyone(self, analytics_service, context):
        result = await question(analytics_service, context, "question-2")

        assert result.accuracy_percentage == 100.0
        assert result.wrong_answer_rate == 0.0  # real zero
        assert result.most_frequent_wrong_answer is None
        assert result.average_time_seconds == 45.0

    async def test_questions_are_ordered_by_id(self, analytics_service, context):
        questions = await analytics_service.aggregate_question_analytics(
            AnalyticsFilters(), context
        )

        assert [q.question_id for q in questions] == ["question-1", "question-2"]


class TestMissingAndIncompleteData:
    async def test_question_with_no_timing_data_reports_null_average(
        self, settings, clock, review_store, context
    ):
        repository = InMemoryAnalyticsRepository(
            [make_attempt("a1")],
            [
                make_response("r1", attempt_id="a1", question_id="q", is_correct=True, time_spent_seconds=None),
                make_response("r2", attempt_id="a1", question_id="q", is_correct=False, time_spent_seconds=None),
            ],
            [make_question("q")],
            review_store=review_store,
        )
        service = AnalyticsService(repository, settings, clock)

        result = await question(service, context, "q")

        assert result.average_time_seconds is None
        assert result.timed_response_count == 0
        assert result.accuracy_percentage == 50.0  # unaffected by missing timing

    async def test_entirely_ungraded_question_has_null_accuracy_but_real_counts(
        self, settings, clock, review_store, context
    ):
        repository = InMemoryAnalyticsRepository(
            [make_attempt("a1")],
            [
                make_response("r1", attempt_id="a1", question_id="q", selected_answer="A", is_correct=None),
                make_response("r2", attempt_id="a1", question_id="q", selected_answer="B", is_correct=None),
            ],
            [make_question("q")],
            review_store=review_store,
        )
        service = AnalyticsService(repository, settings, clock)

        result = await question(service, context, "q")

        assert result.attempt_count == 2
        assert result.graded_count == 0
        assert result.accuracy_percentage is None
        assert result.wrong_answer_rate is None
        assert result.data_state is DataState.OK  # responses exist; grading does not

    async def test_response_whose_attempt_is_missing_is_out_of_scope(
        self, settings, clock, review_store, context
    ):
        """An orphan response cannot be attributed to a course, cohort or date."""
        repository = InMemoryAnalyticsRepository(
            [],
            [make_response("r1", attempt_id="ghost", question_id="q", is_correct=False)],
            [make_question("q")],
            review_store=review_store,
        )
        service = AnalyticsService(repository, settings, clock)

        assert await service.aggregate_question_analytics(AnalyticsFilters(), context) == ()

    async def test_question_missing_from_the_catalogue_still_reports_its_responses(
        self, settings, clock, review_store, context
    ):
        repository = InMemoryAnalyticsRepository(
            [make_attempt("a1")],
            [make_response("r1", attempt_id="a1", question_id="deleted-q", is_correct=False)],
            [],  # question no longer in the catalogue
            review_store=review_store,
        )
        service = AnalyticsService(repository, settings, clock)

        result = await question(service, context, "deleted-q")

        assert result.attempt_count == 1
        assert result.question_type is ReportingQuestionType.OTHER


class TestSingleQuestionEndpointBehaviour:
    async def test_returns_the_question(self, analytics_service, context):
        result = await analytics_service.get_question_analytics(
            "question-1", AnalyticsFilters(), context
        )

        assert result.question.question_id == "question-1"
        assert result.calculated_at == NOW

    async def test_unknown_question_raises_not_found(self, analytics_service, context):
        with pytest.raises(NotFoundError) as exc:
            await analytics_service.get_question_analytics("no-such-q", AnalyticsFilters(), context)

        assert exc.value.code == "NOT_FOUND"
        assert exc.value.http_status == 404

    async def test_known_question_with_no_responses_in_scope_reports_no_attempts(
        self, analytics_service, context
    ):
        result = await analytics_service.get_question_analytics(
            "question-2", AnalyticsFilters(course_id="course-2"), context
        )

        assert result.question.data_state is DataState.NO_ATTEMPTS
        assert result.question.attempt_count == 0
        assert result.question.accuracy_percentage is None

    async def test_single_question_query_is_narrowed_at_the_provider(
        self, analytics_service, repository, context
    ):
        await analytics_service.get_question_analytics("question-1", AnalyticsFilters(), context)

        assert "fetch_responses_page" in repository.call_log


class TestListingAndSorting:
    async def test_pagination_reports_totals(self, analytics_service, context):
        page = await analytics_service.list_question_analytics(
            AnalyticsFilters(), context, limit=1, offset=0
        )

        assert page.page.total == 2
        assert page.page.returned == 1
        assert page.page.has_more is True
        assert page.data_state is DataState.OK

    async def test_second_page_continues_without_overlap(self, analytics_service, context):
        first = await analytics_service.list_question_analytics(
            AnalyticsFilters(), context, limit=1, offset=0
        )
        second = await analytics_service.list_question_analytics(
            AnalyticsFilters(), context, limit=1, offset=1
        )

        assert first.items[0].question_id != second.items[0].question_id
        assert second.page.has_more is False

    async def test_sorting_by_accuracy(self, analytics_service, context):
        ascending = await analytics_service.list_question_analytics(
            AnalyticsFilters(), context, sort_by=QuestionSortField.ACCURACY
        )
        descending = await analytics_service.list_question_analytics(
            AnalyticsFilters(),
            context,
            sort_by=QuestionSortField.ACCURACY,
            direction=SortDirection.DESC,
        )

        assert [q.question_id for q in ascending.items] == ["question-1", "question-2"]
        assert [q.question_id for q in descending.items] == ["question-2", "question-1"]

    async def test_null_metrics_sort_last_in_both_directions(
        self, settings, clock, review_store, context
    ):
        repository = InMemoryAnalyticsRepository(
            [make_attempt("a1")],
            [
                make_response("r1", attempt_id="a1", question_id="graded", is_correct=True),
                make_response("r2", attempt_id="a1", question_id="ungraded", is_correct=None),
            ],
            [make_question("graded"), make_question("ungraded")],
            review_store=review_store,
        )
        service = AnalyticsService(repository, settings, clock)

        for direction in (SortDirection.ASC, SortDirection.DESC):
            page = await service.list_question_analytics(
                AnalyticsFilters(), context, sort_by=QuestionSortField.ACCURACY, direction=direction
            )
            assert page.items[-1].question_id == "ungraded", direction

    async def test_empty_scope_reports_no_attempts_with_no_rows(
        self, analytics_service, context
    ):
        page = await analytics_service.list_question_analytics(
            AnalyticsFilters(course_id="nope"), context
        )

        assert page.items == ()
        assert page.data_state is DataState.NO_ATTEMPTS
        assert page.page.total == 0

    async def test_flagged_only_filter(self, analytics_service, review_store, context):
        await review_store.put_flag(make_flag("question-2"))

        page = await analytics_service.list_question_analytics(
            AnalyticsFilters(), context, flagged_only=True
        )

        assert [q.question_id for q in page.items] == ["question-2"]

    @pytest.mark.parametrize("page_size", [1, 2, 7, 100])
    async def test_results_are_independent_of_repository_page_size(
        self, repository, clock, context, page_size
    ):
        service = AnalyticsService(repository, make_settings(repository_page_size=page_size), clock)

        result = await question(service, context, "question-1")

        assert result.attempt_count == 5
        assert result.accuracy_percentage == 25.0
        assert result.most_frequent_wrong_answer.answer == "B"
