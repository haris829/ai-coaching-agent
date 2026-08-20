"""Remaining edge cases: record properties, auth modes and write-path failures."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.modules.analytics.api.deps import build_container
from app.modules.analytics.config import AnalyticsSettings
from app.modules.analytics.domain.enums import AttemptStatus, ReviewActionType
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.errors import RepositoryUnavailableError
from app.modules.analytics.repositories.in_memory import (
    InMemoryAnalyticsRepository,
    InMemoryReviewRepository,
    InMemoryReviewStore,
)
from app.modules.analytics.services import AnalyticsService, FlagService
from tests.analytics.world import admin_auth_headers, build_analytics_app

from .doubles import FailingAnalyticsRepository, FailingReviewRepository
from .factories import BASE_TIME, make_attempt, make_flag, make_question, make_response

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio


class TestRecordProperties:
    def test_duration_of_an_incomplete_attempt_is_unknown(self):
        attempt = make_attempt("a1", status="IN_PROGRESS", completed_at=None, score=None, passed=None)

        assert attempt.duration_seconds is None

    def test_duration_is_computed_from_the_timestamps(self):
        attempt = make_attempt("a1", started_at=BASE_TIME, completed_at=BASE_TIME + timedelta(minutes=30))

        assert attempt.duration_seconds == 1800.0

    def test_negative_duration_is_rejected_as_unusable(self):
        """A completion before the start is corrupt data, not a negative duration."""
        attempt = make_attempt(
            "a1", started_at=BASE_TIME, completed_at=BASE_TIME - timedelta(minutes=5)
        )

        assert attempt.duration_seconds is None

    def test_completion_follows_status_not_the_timestamp(self):
        """Some providers stamp completed_at when closing out an abandoned session."""
        attempt = make_attempt(
            "a1", status="ABANDONED", completed_at=BASE_TIME + timedelta(minutes=5), score=None, passed=None
        )

        assert attempt.status is AttemptStatus.ABANDONED
        assert attempt.is_completed is False

    def test_scored_and_graded_are_independent(self):
        scored_only = make_attempt("a1", score=55.0, passed=None)

        assert scored_only.is_scored is True
        assert scored_only.is_graded is False

    def test_response_answer_and_grading_flags(self):
        skipped = make_response("r1", selected_answer=None, is_correct=None)
        answered = make_response("r2", selected_answer="A", is_correct=True)

        assert skipped.is_answered is False
        assert skipped.is_graded is False
        assert answered.is_answered is True
        assert answered.is_graded is True

    def test_blank_cohort_is_normalised_to_absent(self):
        assert make_attempt("a1", cohort_id="   ").cohort_id is None

    def test_flag_record_lifecycle_helpers(self):
        active = make_flag("q1")
        retired = make_flag("q2", status="RETIRED")

        assert active.is_active is True
        assert active.is_terminal is False
        assert retired.is_active is False
        assert retired.is_terminal is True

    def test_question_metadata_display_type_prefers_the_provider_label(self):
        known = make_question("q1", question_type="TRUE_FALSE")
        unknown = make_question("q2", question_type="hotspot")

        assert known.display_type == "TRUE_FALSE"
        assert unknown.display_type == "hotspot"

    def test_page_helpers(self):
        from app.modules.analytics.domain.records import AttemptRecord, Page

        page = Page[AttemptRecord](items=(make_attempt("a1"),), next_cursor="2")
        last = Page[AttemptRecord](items=(make_attempt("a2"),))

        assert len(page) == 1
        assert page.has_more is True
        assert last.has_more is False


class TestFilterHelpers:
    def test_cache_key_is_stable_and_distinguishing(self):
        first = AnalyticsFilters(course_id="course-1", cohort_id="cohort-a")
        same = AnalyticsFilters(course_id="course-1", cohort_id="cohort-a")
        different = AnalyticsFilters(course_id="course-2", cohort_id="cohort-a")

        assert first.cache_key() == same.cache_key()
        assert first.cache_key() != different.cache_key()

    def test_with_course_returns_a_new_filter_object(self):
        original = AnalyticsFilters(cohort_id="cohort-a")

        derived = original.with_course("course-9")

        assert derived.course_id == "course-9"
        assert derived.cohort_id == "cohort-a"
        assert original.course_id is None  # unchanged

    def test_analytics_outputs_expose_a_data_state_helper(self, settings):
        from app.modules.analytics.services.aggregation import OverallAccumulator

        from .factories import NOW

        empty = OverallAccumulator().build(
            filters=AnalyticsFilters(), calculated_at=NOW, decimal_places=2
        )
        accumulator = OverallAccumulator()
        accumulator.add(make_attempt("a1"))
        populated = accumulator.build(
            filters=AnalyticsFilters(), calculated_at=NOW, decimal_places=2
        )

        assert empty.has_data is False
        assert populated.has_data is True


class TestAuthenticationModes:
    """Authentication, as the merge left it.

    Standalone, UC-10 could be configured with ``auth_enabled=False`` and would then serve
    unauthenticated reads. That switch is gone, and its absence is the assertion: analytics sits
    behind the one administrator guard, and there is no setting anywhere that can open it. A
    runtime-tunable authentication flag is a runtime-tunable way to disable authentication.

    What is still worth checking here is that the guard is actually attached to every endpoint,
    and that the administrator the audit trail records is the one the guard resolved rather than
    anything a client sent.
    """

    def _app(self, clock, dataset):
        store = InMemoryReviewStore()
        settings = AnalyticsSettings(_env_file=None)
        return build_analytics_app(
            build_container(
                analytics_repository=InMemoryAnalyticsRepository(
                    dataset["attempts"],
                    dataset["responses"],
                    dataset["questions"],
                    review_store=store,
                ),
                review_repository=InMemoryReviewRepository(store),
                settings=settings,
                clock=clock,
            )
        )

    def test_the_settings_carry_no_authentication_switch(self):
        """The strongest form of the old test: the switch does not exist to be turned off."""
        fields = set(AnalyticsSettings.model_fields)
        assert "auth_enabled" not in fields
        assert "admin_api_keys" not in fields

    def test_an_unauthenticated_read_is_refused(self, clock, dataset):
        """There is no configuration that opens analytics to an unauthenticated caller.

        Standalone, ``auth_enabled=False`` served unauthenticated reads and attributed review
        actions to a synthetic principal. Both are gone; this asserts the replacement behaviour
        rather than the removed switch.
        """
        with TestClient(self._app(clock, dataset)) as client:
            response = client.get("/api/admin/analytics/overall")

        assert response.status_code == 401

    def test_a_review_action_is_attributed_to_the_resolved_administrator(
        self, clock, dataset
    ):
        """The audit row names whoever the guard resolved — never anything a client sent."""
        with TestClient(self._app(clock, dataset)) as client:
            response = client.post(
                "/api/admin/analytics/review/actions",
                headers=admin_auth_headers("admin-7"),
                json={"question_id": "question-1", "action": "NO_CHANGE"},
            )

        assert response.status_code == 201, response.text
        assert response.json()["action"]["admin_id"] == "admin-7"


class TestWritePathFailures:
    async def test_flag_persistence_failure_is_reported_as_a_provider_fault(
        self, repository, settings, clock, context
    ):
        analytics = AnalyticsService(repository, settings, clock)
        flags = FlagService(
            analytics, repository, FailingReviewRepository(), settings, clock
        )

        with pytest.raises(RepositoryUnavailableError) as exc:
            await flags.evaluate(AnalyticsFilters(), context)

        assert exc.value.details["operation"] == "upsert_flag"
        assert "timed out" not in exc.value.public_message()

    async def test_fresh_evidence_scan_failure_is_reported_as_a_provider_fault(
        self, review_store, settings, clock, context
    ):
        """The re-flag evidence check reads assessment data and can fail too."""
        await review_store.put_flag(
            make_flag(
                "q1",
                status="RESOLVED",
                resolved_at=BASE_TIME,
                resolved_by="admin-1",
                resolution_action=ReviewActionType.QUESTION_UPDATED,
            )
        )
        healthy = InMemoryAnalyticsRepository(
            [make_attempt("a1")],
            [
                make_response(f"r{i}", attempt_id="a1", question_id="q1", selected_answer="B", is_correct=False)
                for i in range(5)
            ],
            [make_question("q1")],
            review_store=review_store,
        )
        analytics = AnalyticsService(healthy, settings, clock)
        flags = FlagService(
            analytics,
            FailingAnalyticsRepository(),  # the evidence scan uses this one
            InMemoryReviewRepository(review_store),
            settings,
            clock,
        )

        with pytest.raises(RepositoryUnavailableError) as exc:
            await flags.evaluate(AnalyticsFilters(), context)

        assert exc.value.details["operation"] == "fetch_responses_page"

    async def test_review_action_survives_a_flag_read_failure_being_typed(
        self, settings, clock, context
    ):
        from app.modules.analytics.services import ReviewService

        service = ReviewService(FailingReviewRepository(), settings, clock)

        with pytest.raises(RepositoryUnavailableError) as exc:
            await service.get_history("q1", context)

        assert exc.value.http_status == 503


class TestReferenceProviderInternals:
    async def test_malformed_cursor_is_rejected(self, repository, context):
        from app.modules.analytics.domain.records import PageRequest

        for bad in ("not-a-number", "-1"):
            with pytest.raises(ValueError):
                await repository.fetch_attempts_page(
                    AnalyticsFilters(), PageRequest(cursor=bad, limit=5), context
                )

    async def test_cursor_paging_covers_every_record_exactly_once(
        self, repository, context
    ):
        from app.modules.analytics.domain.records import PageRequest

        seen: list[str] = []
        cursor = None
        while True:
            page = await repository.fetch_attempts_page(
                AnalyticsFilters(), PageRequest(cursor=cursor, limit=2), context
            )
            seen.extend(a.attempt_id for a in page.items)
            if not page.has_more:
                break
            cursor = page.next_cursor

        assert sorted(seen) == ["a1", "a2", "a3", "a4", "a5"]
        assert len(seen) == len(set(seen))

    async def test_review_store_snapshots_are_copies(self, review_store):
        await review_store.put_flag(make_flag("q1"))

        snapshot = review_store.flags_snapshot()
        snapshot.clear()

        assert "q1" in review_store.flags_snapshot()

    async def test_health_check_reports_reachable(self, repository, context):
        assert await repository.health_check(context) is True

    def test_fingerprint_changes_when_the_seeded_data_differs(self, dataset, review_store):
        first = InMemoryAnalyticsRepository(
            dataset["attempts"], dataset["responses"], dataset["questions"], review_store=review_store
        )
        second = InMemoryAnalyticsRepository(
            dataset["attempts"][:-1], dataset["responses"], dataset["questions"], review_store=review_store
        )

        assert first.snapshot_fingerprint() != second.snapshot_fingerprint()
