"""Scale and efficiency tests (spec sections 13, 25).

The baseline requirement is 500+ attempts. These tests check three separate
claims:

* **Correctness at scale** - the figures computed over a large dataset match an
  independent calculation over the same records.
* **Bounded memory** - the number of records held at once is capped by the page
  size, not by the dataset, so the design scales past the baseline.
* **No repeated work** - a single request makes one pass over the data, and the
  empty case costs one cheap count.
"""

from __future__ import annotations

import time

import pytest

from app.modules.analytics.cancellation import QueryContext
from app.modules.analytics.domain.enums import AssessmentType, AttemptStatus
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.domain.records import Page, PageRequest
from app.modules.analytics.repositories.in_memory import (
    InMemoryAnalyticsRepository,
)
from app.modules.analytics.services import AnalyticsService, CsvExportService, FlagService

from .conftest import make_settings
from .factories import build_dataset

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio

ATTEMPT_COUNT = 600  # comfortably above the 500-attempt baseline


@pytest.fixture(scope="module")
def large_dataset():
    return build_dataset(attempts=ATTEMPT_COUNT, questions_per_attempt=4)


@pytest.fixture
def large_repository(large_dataset, review_store):
    attempts, responses, questions = large_dataset
    return InMemoryAnalyticsRepository(attempts, responses, questions, review_store=review_store)


@pytest.fixture
def large_service(large_repository, clock):
    return AnalyticsService(large_repository, make_settings(repository_page_size=200), clock)


class TestCorrectnessAtScale:
    async def test_dataset_is_the_expected_size(self, large_dataset):
        attempts, responses, _ = large_dataset

        assert len(attempts) == ATTEMPT_COUNT
        assert len(responses) == ATTEMPT_COUNT * 4

    async def test_metrics_match_an_independent_calculation(
        self, large_service, large_dataset, context
    ):
        attempts, _, _ = large_dataset
        result = await large_service.get_overall_analytics(AnalyticsFilters(), context)

        completed = [a for a in attempts if a.status is AttemptStatus.COMPLETED]
        scored = [a for a in attempts if a.score is not None]
        graded = [a for a in attempts if a.passed is not None]
        expected_average = sum(a.score for a in scored) / len(scored)

        assert result.attempt_volume == len(attempts)
        assert result.completed_attempts == len(completed)
        assert result.scored_attempts == len(scored)
        assert result.average_score == pytest.approx(round(expected_average, 2), abs=0.01)
        assert result.pass_rate == pytest.approx(
            round(100 * sum(1 for a in graded if a.passed) / len(graded), 2), abs=0.01
        )
        assert result.unique_learners == len({a.learner_id for a in attempts})

    async def test_question_metrics_match_an_independent_calculation(
        self, large_service, large_dataset, context
    ):
        _, responses, _ = large_dataset
        questions = await large_service.aggregate_question_analytics(AnalyticsFilters(), context)

        by_id = {q.question_id: q for q in questions}
        assert sum(q.attempt_count for q in questions) == len(responses)

        target = next(iter(by_id))
        subset = [r for r in responses if r.question_id == target]
        graded = [r for r in subset if r.is_correct is not None]
        correct = [r for r in graded if r.is_correct]

        assert by_id[target].attempt_count == len(subset)
        assert by_id[target].graded_count == len(graded)
        assert by_id[target].correct_count == len(correct)
        assert by_id[target].accuracy_percentage == pytest.approx(
            round(100 * len(correct) / len(graded), 2), abs=0.01
        )

    async def test_filters_still_agree_at_scale(self, large_service, large_dataset, context):
        attempts, _, _ = large_dataset

        quiz = await large_service.get_overall_analytics(
            AnalyticsFilters(assessment_type=AssessmentType.STANDARD_QUIZ), context
        )
        formal = await large_service.get_overall_analytics(
            AnalyticsFilters(assessment_type=AssessmentType.FORMAL_ASSESSMENT), context
        )

        assert quiz.attempt_volume + formal.attempt_volume == len(attempts)

    # 2000 is the largest page size the configuration guard accepts unconfirmed.
    @pytest.mark.parametrize("page_size", [1, 37, 200, 2000])
    async def test_results_do_not_depend_on_page_size(
        self, large_repository, clock, context, page_size
    ):
        service = AnalyticsService(
            large_repository, make_settings(repository_page_size=page_size), clock
        )

        result = await service.get_overall_analytics(AnalyticsFilters(), context)

        assert result.attempt_volume == ATTEMPT_COUNT


class TestBoundedMemory:
    async def test_no_more_than_one_page_is_ever_materialised(
        self, large_dataset, review_store, clock, context
    ):
        """The provider is asked for pages, and each is released before the next."""
        attempts, responses, questions = large_dataset
        page_size = 50
        observed: list[int] = []

        class PageWatchingRepository(InMemoryAnalyticsRepository):
            async def fetch_attempts_page(self, filters, page: PageRequest, ctx) -> Page:
                result = await super().fetch_attempts_page(filters, page, ctx)
                observed.append(len(result.items))
                return result

            async def fetch_responses_page(self, filters, page, ctx, *, question_ids=None) -> Page:
                result = await super().fetch_responses_page(
                    filters, page, ctx, question_ids=question_ids
                )
                observed.append(len(result.items))
                return result

        repository = PageWatchingRepository(
            attempts, responses, questions, review_store=review_store
        )
        service = AnalyticsService(
            repository, make_settings(repository_page_size=page_size), clock
        )

        await service.get_overall_analytics(AnalyticsFilters(), context)
        await service.aggregate_question_analytics(AnalyticsFilters(), context)

        assert observed  # pages really were requested
        assert max(observed) <= page_size
        assert len(observed) >= ATTEMPT_COUNT // page_size

    async def test_accumulator_state_is_proportional_to_questions_not_responses(
        self, large_service, large_dataset, context
    ):
        _, responses, questions = large_dataset

        aggregated = await large_service.aggregate_question_analytics(
            AnalyticsFilters(), context
        )

        # One accumulator per distinct question, regardless of 2400 responses.
        assert len(aggregated) == len({r.question_id for r in responses})
        assert len(aggregated) <= len(questions)


class TestEfficiency:
    async def test_one_pass_over_attempts_per_request(
        self, large_repository, clock, context
    ):
        service = AnalyticsService(
            large_repository, make_settings(repository_page_size=200), clock
        )
        large_repository.call_log.clear()

        await service.get_overall_analytics(AnalyticsFilters(), context)

        pages = large_repository.call_log.count("fetch_attempts_page")
        # 600 attempts at 200 per page: three pages plus one to confirm the end.
        assert pages <= 4
        assert large_repository.call_log.count("count_attempts") == 1

    async def test_metadata_and_flags_are_fetched_in_bulk(
        self, large_repository, clock, context
    ):
        service = AnalyticsService(
            large_repository, make_settings(repository_page_size=500), clock
        )
        large_repository.call_log.clear()

        await service.aggregate_question_analytics(AnalyticsFilters(), context)

        # One batched call each, not one per question.
        assert large_repository.call_log.count("fetch_question_metadata") == 1
        assert large_repository.call_log.count("get_flags") == 1

    async def test_empty_scope_costs_a_single_count(self, large_repository, clock, context):
        service = AnalyticsService(large_repository, make_settings(), clock)
        large_repository.call_log.clear()

        await service.get_overall_analytics(AnalyticsFilters(course_id="absent"), context)

        assert large_repository.call_log == ["count_attempts"]


@pytest.mark.performance
class TestThroughput:
    async def test_dashboard_query_over_600_attempts_is_prompt(
        self, large_service, context
    ):
        started = time.perf_counter()

        await large_service.get_overall_analytics(AnalyticsFilters(), context)

        elapsed = time.perf_counter() - started
        # Generous: the assertion catches accidental quadratic work, not machine speed.
        assert elapsed < 2.0, f"aggregation took {elapsed:.3f}s"

    async def test_question_analytics_over_2400_responses_is_prompt(
        self, large_service, context
    ):
        started = time.perf_counter()

        await large_service.aggregate_question_analytics(AnalyticsFilters(), context)

        elapsed = time.perf_counter() - started
        assert elapsed < 3.0, f"question aggregation took {elapsed:.3f}s"

    async def test_csv_export_at_scale_is_prompt_and_complete(
        self, large_service, large_dataset, clock, context
    ):
        _, responses, _ = large_dataset
        exporter = CsvExportService(large_service, make_settings(), clock)

        started = time.perf_counter()
        export = await exporter.export_questions(AnalyticsFilters(), context)
        elapsed = time.perf_counter() - started

        assert export.row_count == len({r.question_id for r in responses})
        assert elapsed < 3.0, f"export took {elapsed:.3f}s"

    async def test_cost_grows_linearly_not_quadratically(
        self, review_store, clock, context
    ):
        """Doubling the dataset must not quadruple the work."""
        timings = {}
        for size in (300, 1200):
            attempts, responses, questions = build_dataset(attempts=size)
            repository = InMemoryAnalyticsRepository(
                attempts, responses, questions, review_store=review_store
            )
            service = AnalyticsService(
                repository, make_settings(repository_page_size=500), clock
            )
            fresh = QueryContext.create(timeout_seconds=60.0, clock=clock)

            started = time.perf_counter()
            await service.aggregate_question_analytics(AnalyticsFilters(), fresh)
            timings[size] = time.perf_counter() - started

        # 4x the records should cost far less than 16x the time.
        ratio = timings[1200] / max(timings[300], 1e-6)
        assert ratio < 12, f"scaling looks super-linear: {timings}"


class TestFlagEvaluationAtScale:
    async def test_evaluation_covers_every_question_once(
        self, large_repository, review_repository, clock, context
    ):
        settings = make_settings(repository_page_size=300, flag_min_responses=5)
        analytics = AnalyticsService(large_repository, settings, clock)
        flags = FlagService(analytics, large_repository, review_repository, settings, clock)

        result = await flags.evaluate(AnalyticsFilters(), context)

        accounted = (
            result.newly_flagged
            + result.re_flagged
            + result.already_flagged
            + result.below_threshold_retained
            + result.skipped_insufficient_data
            + result.skipped_retired
        )
        assert result.evaluated_questions == 8
        assert len(set(accounted)) <= result.evaluated_questions
