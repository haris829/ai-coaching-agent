"""Content-review flag tests (spec sections 8, 18, 25).

The persistence rules are the delicate part: a flag must survive recalculation,
must not be re-raised while active, and must come back only on genuinely new
evidence after an administrator resolved it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.modules.analytics.cancellation import QueryContext
from app.modules.analytics.domain.enums import FlagReason, FlagStatus, ReviewActionType
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.domain.review import ReviewActionRequest
from app.modules.analytics.repositories.in_memory import (
    InMemoryAnalyticsRepository,
    InMemoryReviewRepository,
)
from app.modules.analytics.services import AnalyticsService, FlagService

from .conftest import ADMIN_ID, make_settings
from .factories import BASE_TIME, NOW, make_attempt, make_flag, make_question, make_response

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio


def build_responses(
    question_id: str,
    *,
    wrong: int,
    correct: int,
    answered_at=None,
    prefix: str = "r",
):
    """Responses for one question with an exact wrong/correct split."""
    records = []
    for index in range(wrong):
        records.append(
            make_response(
                f"{prefix}-w{index}",
                attempt_id="a1",
                question_id=question_id,
                selected_answer="B",
                is_correct=False,
                answered_at=answered_at,
            )
        )
    for index in range(correct):
        records.append(
            make_response(
                f"{prefix}-c{index}",
                attempt_id="a1",
                question_id=question_id,
                selected_answer="A",
                is_correct=True,
                answered_at=answered_at,
            )
        )
    return records


def make_environment(responses, review_store, settings, clock, questions=("q1",)):
    repository = InMemoryAnalyticsRepository(
        [make_attempt("a1")],
        responses,
        [make_question(q) for q in questions],
        review_store=review_store,
    )
    analytics = AnalyticsService(repository, settings, clock)
    review_repository = InMemoryReviewRepository(review_store)
    flags = FlagService(analytics, repository, review_repository, settings, clock)
    return repository, analytics, review_repository, flags


class TestThresholdLogic:
    async def test_question_above_threshold_is_flagged(
        self, review_store, settings, clock, context
    ):
        _, _, _, flags = make_environment(
            build_responses("q1", wrong=4, correct=1), review_store, settings, clock
        )

        result = await flags.evaluate(AnalyticsFilters(), context)

        assert result.newly_flagged == ("q1",)
        assert review_store.flags_snapshot()["q1"].status is FlagStatus.FLAGGED

    async def test_question_below_threshold_is_not_flagged(
        self, review_store, settings, clock, context
    ):
        _, _, _, flags = make_environment(
            build_responses("q1", wrong=1, correct=4), review_store, settings, clock
        )

        result = await flags.evaluate(AnalyticsFilters(), context)

        assert result.newly_flagged == ()
        assert review_store.flags_snapshot() == {}

    async def test_rate_exactly_at_threshold_is_not_flagged(
        self, review_store, clock, context
    ):
        """The threshold is a strict lower bound: 40% does not exceed 40%."""
        settings = make_settings(flag_wrong_answer_rate_threshold=40.0, flag_min_responses=3)
        _, analytics, _, flags = make_environment(
            build_responses("q1", wrong=2, correct=3), review_store, settings, clock
        )

        questions = await analytics.aggregate_question_analytics(AnalyticsFilters(), context)
        result = await flags.evaluate(AnalyticsFilters(), context)

        assert questions[0].wrong_answer_rate == 40.0
        assert result.newly_flagged == ()

    async def test_threshold_is_configurable(self, review_store, clock, context):
        responses = build_responses("q1", wrong=3, correct=7)  # 30% wrong

        strict = make_settings(flag_wrong_answer_rate_threshold=25.0)
        _, _, _, flags = make_environment(responses, review_store, strict, clock)
        assert (await flags.evaluate(AnalyticsFilters(), context)).newly_flagged == ("q1",)

        lenient_store = type(review_store)()
        lenient = make_settings(flag_wrong_answer_rate_threshold=35.0)
        _, _, _, lenient_flags = make_environment(responses, lenient_store, lenient, clock)
        assert (await lenient_flags.evaluate(AnalyticsFilters(), context)).newly_flagged == ()

    async def test_small_sample_is_not_flagged_and_is_reported_as_such(
        self, review_store, clock, context
    ):
        settings = make_settings(flag_min_responses=5)
        _, _, _, flags = make_environment(
            build_responses("q1", wrong=2, correct=0), review_store, settings, clock
        )

        result = await flags.evaluate(AnalyticsFilters(), context)

        assert result.newly_flagged == ()
        assert result.skipped_insufficient_data == ("q1",)

    async def test_evaluation_reports_the_configuration_it_used(
        self, review_store, clock, context
    ):
        settings = make_settings(flag_wrong_answer_rate_threshold=55.5, flag_min_responses=7)
        _, _, _, flags = make_environment(
            build_responses("q1", wrong=1, correct=1), review_store, settings, clock
        )

        result = await flags.evaluate(AnalyticsFilters(), context)

        assert result.threshold_used == 55.5
        assert result.min_responses_required == 7
        assert result.calculated_at == NOW


class TestFlagRecordContents:
    async def test_flag_captures_the_measurement_that_raised_it(
        self, review_store, settings, clock, context
    ):
        _, _, _, flags = make_environment(
            build_responses("q1", wrong=4, correct=1), review_store, settings, clock
        )

        await flags.evaluate(AnalyticsFilters(), context, triggered_by="scheduler")
        flag = review_store.flags_snapshot()["q1"]

        assert flag.wrong_answer_rate == 80.0
        assert flag.threshold_used == settings.flag_wrong_answer_rate_threshold
        assert flag.graded_responses_at_flag == 5
        assert flag.reason is FlagReason.WRONG_ANSWER_RATE_EXCEEDED
        assert flag.flagged_at == NOW
        assert flag.flagged_by == "scheduler"
        assert flag.resolved_at is None


class TestFlagPersistence:
    async def test_re_evaluation_does_not_re_raise_an_active_flag(
        self, review_store, settings, clock, context
    ):
        _, _, _, flags = make_environment(
            build_responses("q1", wrong=4, correct=1), review_store, settings, clock
        )
        await flags.evaluate(AnalyticsFilters(), context)
        original = review_store.flags_snapshot()["q1"]

        # Advance time and issue a fresh context: the previous one's deadline was
        # measured against the old instant and is now legitimately expired.
        clock.set(NOW + timedelta(days=1))
        later = QueryContext.create(timeout_seconds=30.0, clock=clock)
        result = await flags.evaluate(AnalyticsFilters(), later)

        assert result.newly_flagged == ()
        assert result.already_flagged == ("q1",)
        assert review_store.flags_snapshot()["q1"] == original  # untouched

    async def test_improved_performance_does_not_clear_the_flag(
        self, review_store, settings, clock, context
    ):
        """Recalculation must never erase a flag (spec section 18)."""
        _, _, _, flags = make_environment(
            build_responses("q1", wrong=4, correct=1), review_store, settings, clock
        )
        await flags.evaluate(AnalyticsFilters(), context)

        improved = build_responses("q1", wrong=4, correct=1) + build_responses(
            "q1", wrong=0, correct=40, prefix="later"
        )
        _, analytics, _, improved_flags = make_environment(
            improved, review_store, settings, clock
        )
        questions = await analytics.aggregate_question_analytics(AnalyticsFilters(), context)
        result = await improved_flags.evaluate(AnalyticsFilters(), context)

        assert questions[0].wrong_answer_rate < settings.flag_wrong_answer_rate_threshold
        assert result.below_threshold_retained == ("q1",)
        assert review_store.flags_snapshot()["q1"].status is FlagStatus.FLAGGED

    async def test_flag_survives_a_filter_change_that_hides_its_responses(
        self, analytics_service, review_store, context
    ):
        await review_store.put_flag(make_flag("question-1"))

        result = await analytics_service.get_flagged_questions(
            AnalyticsFilters(course_id="course-does-not-exist"), context
        )

        assert [q.question_id for q in result.items] == ["question-1"]
        assert result.items[0].is_flagged is True
        assert result.items[0].attempt_count == 0

    async def test_analytics_reads_never_create_flags(
        self, review_store, settings, clock, context
    ):
        _, analytics, _, _ = make_environment(
            build_responses("q1", wrong=5, correct=0), review_store, settings, clock
        )

        await analytics.aggregate_question_analytics(AnalyticsFilters(), context)
        await analytics.get_flagged_questions(AnalyticsFilters(), context, include_candidates=True)

        assert review_store.flags_snapshot() == {}

    async def test_candidates_are_visible_before_being_persisted(
        self, review_store, settings, clock, context
    ):
        _, analytics, _, _ = make_environment(
            build_responses("q1", wrong=5, correct=0), review_store, settings, clock
        )

        queue = await analytics.get_flagged_questions(
            AnalyticsFilters(), context, include_candidates=True
        )

        assert [q.question_id for q in queue.items] == ["q1"]
        assert queue.items[0].meets_flag_criteria is True
        assert queue.items[0].is_flagged is False  # nothing persisted
        assert queue.includes_unpersisted_candidates is True


class TestReflagAfterResolution:
    async def _flag_then_resolve(self, review_store, settings, clock, context, review_service):
        _, _, _, flags = make_environment(
            build_responses("q1", wrong=4, correct=1, answered_at=BASE_TIME),
            review_store,
            settings,
            clock,
        )
        await flags.evaluate(AnalyticsFilters(), context)
        await review_service.record_action(
            ReviewActionRequest(question_id="q1", action=ReviewActionType.QUESTION_UPDATED),
            ADMIN_ID,
            context,
        )
        return flags

    async def test_resolved_flag_is_not_re_raised_on_the_same_evidence(
        self, review_store, settings, clock, context, review_service
    ):
        flags = await self._flag_then_resolve(
            review_store, settings, clock, context, review_service
        )

        result = await flags.evaluate(AnalyticsFilters(), context)

        assert result.re_flagged == ()
        assert review_store.flags_snapshot()["q1"].status is FlagStatus.RESOLVED

    async def test_resolved_flag_returns_once_enough_new_evidence_arrives(
        self, review_store, settings, clock, context, review_service
    ):
        await self._flag_then_resolve(review_store, settings, clock, context, review_service)

        fresh = build_responses(
            "q1", wrong=4, correct=1, answered_at=BASE_TIME
        ) + build_responses(
            "q1", wrong=3, correct=0, answered_at=NOW + timedelta(days=1), prefix="fresh"
        )
        _, _, _, flags = make_environment(fresh, review_store, settings, clock)

        result = await flags.evaluate(AnalyticsFilters(), context)

        assert result.re_flagged == ("q1",)
        reraised = review_store.flags_snapshot()["q1"]
        assert reraised.status is FlagStatus.FLAGGED
        assert reraised.resolved_at is None  # clean review cycle
        assert reraised.resolution_action is None

    async def test_insufficient_new_evidence_leaves_the_resolution_standing(
        self, review_store, clock, context, review_service
    ):
        settings = make_settings(reflag_min_new_responses=10)
        await self._flag_then_resolve(review_store, settings, clock, context, review_service)

        fresh = build_responses(
            "q1", wrong=4, correct=1, answered_at=BASE_TIME
        ) + build_responses(
            "q1", wrong=2, correct=0, answered_at=NOW + timedelta(days=1), prefix="fresh"
        )
        _, _, _, flags = make_environment(fresh, review_store, settings, clock)

        result = await flags.evaluate(AnalyticsFilters(), context)

        assert result.re_flagged == ()
        assert review_store.flags_snapshot()["q1"].status is FlagStatus.RESOLVED

    async def test_reflagging_can_be_switched_off(
        self, review_store, clock, context, review_service
    ):
        settings = make_settings(reflag_enabled=False)
        await self._flag_then_resolve(review_store, settings, clock, context, review_service)

        fresh = build_responses(
            "q1", wrong=4, correct=1, answered_at=BASE_TIME
        ) + build_responses(
            "q1", wrong=9, correct=0, answered_at=NOW + timedelta(days=1), prefix="fresh"
        )
        _, _, _, flags = make_environment(fresh, review_store, settings, clock)

        assert (await flags.evaluate(AnalyticsFilters(), context)).re_flagged == ()

    async def test_undated_responses_never_count_as_new_evidence(
        self, review_store, settings, clock, context, review_service
    ):
        await self._flag_then_resolve(review_store, settings, clock, context, review_service)

        undated = [
            make_response(
                f"undated-{index}",
                attempt_id="a1",
                question_id="q1",
                selected_answer="B",
                is_correct=False,
                answered_at=None,
            )
            for index in range(10)
        ] + build_responses("q1", wrong=4, correct=1, answered_at=BASE_TIME)
        _, _, _, flags = make_environment(undated, review_store, settings, clock)

        assert (await flags.evaluate(AnalyticsFilters(), context)).re_flagged == ()


class TestRetirement:
    async def test_retired_question_is_never_flagged_again(
        self, review_store, settings, clock, context
    ):
        await review_store.put_flag(make_flag("q1", status=FlagStatus.RETIRED))
        _, _, _, flags = make_environment(
            build_responses("q1", wrong=10, correct=0), review_store, settings, clock
        )

        result = await flags.evaluate(AnalyticsFilters(), context)

        assert result.skipped_retired == ("q1",)
        assert result.newly_flagged == ()
        assert review_store.flags_snapshot()["q1"].status is FlagStatus.RETIRED


class TestEvaluationAccounting:
    async def test_every_question_appears_in_exactly_one_bucket(
        self, review_store, settings, clock, context
    ):
        responses = (
            build_responses("bad", wrong=5, correct=0, prefix="bad")
            + build_responses("good", wrong=0, correct=5, prefix="good")
            + build_responses("thin", wrong=1, correct=0, prefix="thin")
        )
        _, _, _, flags = make_environment(
            responses, review_store, settings, clock, questions=("bad", "good", "thin")
        )

        result = await flags.evaluate(AnalyticsFilters(), context)

        assert result.evaluated_questions == 3
        assert result.newly_flagged == ("bad",)
        assert result.skipped_insufficient_data == ("thin",)
        buckets = (
            result.newly_flagged
            + result.re_flagged
            + result.already_flagged
            + result.below_threshold_retained
            + result.skipped_insufficient_data
            + result.skipped_retired
        )
        assert len(buckets) == len(set(buckets))
