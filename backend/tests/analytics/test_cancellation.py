"""Cancellation, deadline and filter-refinement tests (spec sections 15, 25)."""

from __future__ import annotations

import asyncio

import pytest

from app.core.time import FixedClock
from app.modules.analytics.cancellation import QueryContext, run_with_deadline
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.errors import QueryCancelledError, QueryTimeoutError
from app.modules.analytics.services import AnalyticsService

from .doubles import HangingAnalyticsRepository
from .factories import NOW

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio


class TestQueryContext:
    def test_unbounded_context_has_no_deadline(self):
        context = QueryContext.create()

        assert context.deadline is None
        assert context.remaining_seconds() is None
        assert context.expired() is False
        context.raise_if_stopped()  # does not raise

    def test_deadline_is_measured_against_the_injected_clock(self):
        clock = FixedClock(NOW)
        context = QueryContext.create(timeout_seconds=30.0, clock=clock)

        assert context.remaining_seconds() == pytest.approx(30.0)

    def test_advancing_the_clock_expires_the_context(self):
        from datetime import timedelta

        clock = FixedClock(NOW)
        context = QueryContext.create(timeout_seconds=30.0, clock=clock)

        clock.set(NOW + timedelta(seconds=31))

        assert context.expired() is True
        with pytest.raises(QueryTimeoutError):
            context.raise_if_stopped()

    def test_zero_or_negative_timeout_is_rejected(self):
        with pytest.raises(ValueError):
            QueryContext.create(timeout_seconds=0)
        with pytest.raises(ValueError):
            QueryContext.create(timeout_seconds=-1)

    def test_cancellation_is_recorded_with_a_reason(self):
        context = QueryContext.create()

        context.cancel("user refined the filters")

        assert context.cancelled is True
        assert context.cancel_reason == "user refined the filters"
        with pytest.raises(QueryCancelledError) as exc:
            context.raise_if_stopped()
        assert exc.value.message == "user refined the filters"

    def test_cancellation_is_idempotent(self):
        context = QueryContext.create()

        context.cancel("first")
        context.cancel("second")

        assert context.cancel_reason == "first"

    async def test_stop_checks_trigger_cancellation(self):
        context = QueryContext.create()
        context.add_stop_check(lambda: asyncio.sleep(0, result=True))

        with pytest.raises(QueryCancelledError) as exc:
            await context.acheck()

        assert "client disconnected" in exc.value.message

    async def test_stop_checks_that_return_false_do_not_cancel(self):
        context = QueryContext.create()
        context.add_stop_check(lambda: asyncio.sleep(0, result=False))

        await context.acheck()

        assert context.cancelled is False

    def test_child_context_shares_the_deadline_and_request_id(self):
        parent = QueryContext.create(timeout_seconds=10.0, request_id="req-1")

        child = parent.child()

        assert child.request_id == "req-1"
        assert child.deadline == parent.deadline

    def test_describe_is_diagnostic_only(self):
        context = QueryContext.create(timeout_seconds=10.0, request_id="req-1")

        described = context.describe()

        assert described["request_id"] == "req-1"
        assert described["cancelled"] is False
        assert described["remaining_seconds"] > 0


class TestRunWithDeadline:
    async def test_returns_the_result_when_work_finishes_in_time(self):
        async def work():
            return 42

        assert await run_with_deadline(work(), QueryContext.create(timeout_seconds=5)) == 42

    async def test_raises_on_deadline_expiry(self):
        with pytest.raises(QueryTimeoutError):
            await run_with_deadline(asyncio.sleep(5), QueryContext.create(timeout_seconds=0.05))

    async def test_raises_when_cancelled_mid_flight(self):
        context = QueryContext.create(timeout_seconds=5)
        task = asyncio.ensure_future(run_with_deadline(asyncio.sleep(5), context))
        await asyncio.sleep(0.01)

        context.cancel("filters refined")

        with pytest.raises(QueryCancelledError):
            await task

    async def test_underlying_work_is_cancelled_not_left_running(self):
        started = asyncio.Event()
        finished = False

        async def work():
            nonlocal finished
            started.set()
            await asyncio.sleep(1)
            finished = True  # pragma: no cover - must never be reached

        context = QueryContext.create(timeout_seconds=5)
        task = asyncio.ensure_future(run_with_deadline(work(), context))
        await started.wait()
        context.cancel()

        with pytest.raises(QueryCancelledError):
            await task
        await asyncio.sleep(0.02)
        assert finished is False

    async def test_repository_errors_propagate_unchanged(self):
        async def work():
            raise KeyError("provider bug")

        with pytest.raises(KeyError):
            await run_with_deadline(work(), QueryContext.create(timeout_seconds=5))

    async def test_already_cancelled_context_does_not_start_the_work(self):
        started = False

        async def work():
            nonlocal started
            started = True  # pragma: no cover
            return 1

        context = QueryContext.create(timeout_seconds=5)
        context.cancel()

        with pytest.raises(QueryCancelledError):
            await run_with_deadline(work(), context)
        assert started is False


class TestServiceLevelCancellation:
    async def test_cancelled_context_stops_an_analytics_query(
        self, analytics_service, context
    ):
        context.cancel("user navigated away")

        with pytest.raises(QueryCancelledError):
            await analytics_service.get_overall_analytics(AnalyticsFilters(), context)

    async def test_cancelled_context_stops_question_aggregation(
        self, analytics_service, context
    ):
        context.cancel()

        with pytest.raises(QueryCancelledError):
            await analytics_service.aggregate_question_analytics(AnalyticsFilters(), context)

    async def test_refining_filters_cancels_the_first_query_and_the_second_succeeds(
        self, repository, settings, clock
    ):
        """The filter-refinement flow: abandon the broad query, run the narrow one."""
        broad_context = QueryContext.create(timeout_seconds=30.0)
        service = AnalyticsService(repository, settings, clock)

        broad_context.cancel("superseded by a narrower query")
        with pytest.raises(QueryCancelledError):
            await service.get_overall_analytics(AnalyticsFilters(), broad_context)

        narrow_context = QueryContext.create(timeout_seconds=30.0)
        result = await service.get_overall_analytics(
            AnalyticsFilters(course_id="course-1"), narrow_context
        )

        assert result.attempt_volume == 4

    async def test_cancellation_of_one_query_does_not_affect_another(
        self, analytics_service, clock
    ):
        first = QueryContext.create(timeout_seconds=30.0, clock=clock)
        second = QueryContext.create(timeout_seconds=30.0, clock=clock)

        first.cancel()

        assert second.cancelled is False
        assert (
            await analytics_service.get_overall_analytics(AnalyticsFilters(), second)
        ).attempt_volume == 5

    async def test_slow_provider_is_cut_off_at_the_deadline(self, settings, clock):
        service = AnalyticsService(HangingAnalyticsRepository(delay=10), settings, clock)
        context = QueryContext.create(timeout_seconds=0.05)

        with pytest.raises(QueryTimeoutError):
            await service.aggregate_question_analytics(AnalyticsFilters(), context)

    async def test_cancelling_a_flag_evaluation_writes_nothing(
        self, flag_service, review_store, context
    ):
        context.cancel("operator aborted the run")

        with pytest.raises(QueryCancelledError):
            await flag_service.evaluate(AnalyticsFilters(), context)

        assert review_store.flags_snapshot() == {}


class TestApiCancellationContract:
    def test_query_context_dependency_wires_the_configured_timeout(self, app):
        container = app.state.analytics.build(None)

        assert container.settings.query_timeout_seconds == 30.0

    def test_client_disconnect_is_registered_as_a_stop_check(self, app):
        """The API contract for cancellation: a dropped client stops the work."""
        import inspect

        from app.modules.analytics.api.deps import get_query_context

        source = inspect.getsource(get_query_context)

        assert "add_stop_check" in source
        assert "is_disconnected" in source
