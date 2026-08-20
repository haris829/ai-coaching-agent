"""Dashboard metric tests against the service and the reference provider.

Expected values are derived by hand from the ``dataset`` fixture, which is
documented record by record in ``conftest.py``.
"""

from __future__ import annotations

import pytest

from app.modules.analytics.domain.enums import AnalyticsScope, DataState
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.repositories.in_memory import InMemoryAnalyticsRepository
from app.modules.analytics.services import AnalyticsService

from .conftest import make_settings
from .factories import NOW, make_attempt

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio


class TestPlatformAnalytics:
    async def test_headline_metrics(self, analytics_service, context):
        result = await analytics_service.get_overall_analytics(AnalyticsFilters(), context)

        assert result.scope is AnalyticsScope.PLATFORM
        assert result.course_id is None
        assert result.attempt_volume == 5
        assert result.completed_attempts == 3
        assert result.completion_rate == 60.0  # 3 of 5
        assert result.scored_attempts == 3
        assert result.average_score == pytest.approx(63.33)  # (90+40+60)/3
        assert result.graded_attempts == 3
        assert result.passed_attempts == 2
        assert result.failed_attempts == 1
        assert result.pass_rate == pytest.approx(66.67)  # 2 of 3
        assert result.unique_learners == 4
        assert result.data_state is DataState.OK

    async def test_response_carries_calculated_at(self, analytics_service, context):
        result = await analytics_service.get_overall_analytics(AnalyticsFilters(), context)

        assert result.calculated_at == NOW
        assert result.calculated_at.tzinfo is not None

    async def test_response_echoes_the_filters_it_used(self, analytics_service, context):
        filters = AnalyticsFilters(course_id="course-1", cohort_id="cohort-a")

        result = await analytics_service.get_overall_analytics(filters, context)

        assert result.filters == filters


class TestCourseAnalytics:
    async def test_course_scope_narrows_every_metric(self, analytics_service, context):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(course_id="course-1"), context
        )

        assert result.scope is AnalyticsScope.COURSE
        assert result.course_id == "course-1"
        assert result.attempt_volume == 4  # a1, a2, a3, a5
        assert result.completed_attempts == 2
        assert result.completion_rate == 50.0
        assert result.average_score == 65.0  # (90+40)/2
        assert result.pass_rate == 50.0
        assert result.unique_learners == 3

    async def test_second_course_is_independent(self, analytics_service, context):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(course_id="course-2"), context
        )

        assert result.attempt_volume == 1
        assert result.average_score == 60.0
        assert result.pass_rate == 100.0
        assert result.completion_rate == 100.0


class TestZeroAttemptState:
    async def test_unknown_course_reports_no_attempts_and_no_numbers(
        self, analytics_service, context
    ):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(course_id="course-does-not-exist"), context
        )

        assert result.data_state is DataState.NO_ATTEMPTS
        assert result.attempt_volume == 0
        assert result.average_score is None
        assert result.pass_rate is None
        assert result.completion_rate is None
        assert result.unique_learners == 0

    async def test_no_attempts_state_still_reports_freshness_and_scope(
        self, analytics_service, context
    ):
        result = await analytics_service.get_overall_analytics(
            AnalyticsFilters(course_id="nope"), context
        )

        assert result.calculated_at == NOW
        assert result.scope is AnalyticsScope.COURSE

    async def test_empty_scope_is_detected_without_scanning_attempts(
        self, analytics_service, repository, context
    ):
        await analytics_service.get_overall_analytics(
            AnalyticsFilters(course_id="nope"), context
        )

        assert repository.call_log == ["count_attempts"]

    async def test_real_zero_is_distinguishable_from_absent_data(
        self, settings, clock, review_store, context
    ):
        """A cohort that attempted but never completed scores a real 0%, not null."""
        repository = InMemoryAnalyticsRepository(
            [
                make_attempt("a1", status="ABANDONED", score=None, passed=None),
                make_attempt("a2", status="IN_PROGRESS", score=None, passed=None),
            ],
            [],
            [],
            review_store=review_store,
        )
        service = AnalyticsService(repository, settings, clock)

        result = await service.get_overall_analytics(AnalyticsFilters(), context)

        assert result.data_state is DataState.OK
        assert result.completion_rate == 0.0
        assert result.average_score is None
        assert result.pass_rate is None


class TestDataQualityNotes:
    async def test_notes_report_attempts_excluded_from_metrics(
        self, analytics_service, context
    ):
        result = await analytics_service.get_overall_analytics(AnalyticsFilters(), context)

        joined = " ".join(result.notes)
        assert "2 of 5 attempts carry no score" in joined
        assert "2 of 5 attempts have no pass/fail outcome" in joined

    async def test_complete_data_produces_no_notes(self, settings, clock, review_store, context):
        repository = InMemoryAnalyticsRepository(
            [make_attempt("a1", score=80.0, passed=True)], [], [], review_store=review_store
        )
        service = AnalyticsService(repository, settings, clock)

        result = await service.get_overall_analytics(AnalyticsFilters(), context)

        assert result.notes == ()


class TestPagination:
    @pytest.mark.parametrize("page_size", [1, 2, 3, 5, 100])
    async def test_metrics_are_independent_of_repository_page_size(
        self, repository, clock, context, page_size
    ):
        service = AnalyticsService(repository, make_settings(repository_page_size=page_size), clock)

        result = await service.get_overall_analytics(AnalyticsFilters(), context)

        assert result.attempt_volume == 5
        assert result.average_score == pytest.approx(63.33)
        assert result.unique_learners == 4
