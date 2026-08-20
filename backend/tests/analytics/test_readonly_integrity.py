"""Read-only integrity tests (spec sections 17, 25).

The requirement is that UC-10 never modifies assessment data. Three independent
lines of evidence are checked:

1. **Structural** - the read interface exposes no mutating method, and no service
   that touches assessment data holds a reference to a write interface.
2. **Behavioural** - a proxy records every repository call made during a full
   sweep of analytics operations; none of them is a write.
3. **Data-level** - a fingerprint of all attempts, responses and question
   metadata is identical before and after that sweep.
"""

from __future__ import annotations

import inspect

import pytest

from app.modules.analytics.domain.enums import ReviewActionType
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.domain.review import ReviewActionRequest
from app.modules.analytics.repositories.base import (
    AnalyticsRepository,
    ReviewRepository,
    assert_read_only,
)
from app.modules.analytics.services import AnalyticsService, CsvExportService, FlagService
from app.modules.analytics.services.analytics_service import AnalyticsService as ServiceClass

from .conftest import ADMIN_ID, AUTH_HEADERS
from .doubles import RecordingRepository
from .factories import make_flag

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio


class TestStructuralGuarantees:
    def test_analytics_repository_exposes_no_mutating_method(self):
        assert_read_only(AnalyticsRepository)

    def test_the_guard_would_catch_a_widened_interface(self):
        class WidenedRepository(AnalyticsRepository):
            async def update_attempt(self, attempt_id: str) -> None: ...

        with pytest.raises(AssertionError):
            assert_read_only(WidenedRepository)

    def test_review_repository_is_the_only_write_surface(self):
        write_methods = {
            name
            for name in ReviewRepository.__abstractmethods__
            if name in {"upsert_flag", "record_action"}
        }

        assert write_methods == {"upsert_flag", "record_action"}

    def test_analytics_service_holds_no_write_interface(self, analytics_service):
        for attribute in vars(analytics_service).values():
            assert not isinstance(attribute, ReviewRepository)

    def test_analytics_service_source_never_calls_a_write_method(self):
        source = inspect.getsource(ServiceClass)

        for forbidden in ("upsert_flag", "record_action", "delete", "insert", "update("):
            assert forbidden not in source, forbidden

    def test_attempt_and_response_records_are_immutable(self, dataset):
        from pydantic import ValidationError

        attempt = dataset["attempts"][0]
        response = dataset["responses"][0]

        with pytest.raises(ValidationError):
            attempt.score = 100.0
        with pytest.raises(ValidationError):
            response.is_correct = True


class TestBehaviouralGuarantees:
    async def _run_every_read_operation(self, repository, settings, clock, context):
        analytics = AnalyticsService(repository, settings, clock)
        exporter = CsvExportService(analytics, settings, clock)
        filters = AnalyticsFilters()

        await analytics.get_overall_analytics(filters, context)
        await analytics.get_overall_analytics(AnalyticsFilters(course_id="course-1"), context)
        await analytics.aggregate_question_analytics(filters, context)
        await analytics.list_question_analytics(filters, context)
        await analytics.get_question_analytics("question-1", filters, context)
        await analytics.get_flagged_questions(filters, context)
        await analytics.get_flagged_questions(filters, context, include_candidates=True)
        await analytics.check_provider_health(context)
        await exporter.export_overall(filters, context)
        await exporter.export_questions(filters, context)
        await exporter.export_flagged_questions(filters, context)

    async def test_no_write_call_is_made_during_any_analytics_operation(
        self, repository, settings, clock, context
    ):
        recording = RecordingRepository(repository)

        await self._run_every_read_operation(recording, settings, clock, context)

        assert recording.mutating_calls == []
        assert set(recording.calls) <= {
            "count_attempts",
            "fetch_attempts_page",
            "fetch_responses_page",
            "fetch_question_metadata",
            "get_flags",
            "get_flag",
            "health_check",
        }

    async def test_assessment_data_is_byte_identical_afterwards(
        self, repository, settings, clock, context
    ):
        before = repository.snapshot_fingerprint()

        await self._run_every_read_operation(repository, settings, clock, context)

        assert repository.snapshot_fingerprint() == before

    async def test_flag_evaluation_leaves_assessment_data_untouched(
        self, repository, review_repository, settings, clock, context
    ):
        analytics = AnalyticsService(repository, settings, clock)
        flags = FlagService(analytics, repository, review_repository, settings, clock)
        before = repository.snapshot_fingerprint()

        await flags.evaluate(AnalyticsFilters(), context)

        assert repository.snapshot_fingerprint() == before

    async def test_review_actions_leave_assessment_data_untouched(
        self, repository, review_service, review_store, context
    ):
        await review_store.put_flag(make_flag("question-1"))
        before = repository.snapshot_fingerprint()

        await review_service.record_action(
            ReviewActionRequest(
                question_id="question-1", action=ReviewActionType.QUESTION_RETIRED
            ),
            ADMIN_ID,
            context,
        )

        assert repository.snapshot_fingerprint() == before

    async def test_writes_are_confined_to_the_review_store(
        self, repository, review_repository, settings, clock, context
    ):
        """Flag evaluation writes flags - and only flags."""
        analytics = AnalyticsService(repository, settings, clock)
        flags = FlagService(analytics, repository, review_repository, settings, clock)
        attempts_before = repository.snapshot_fingerprint()

        result = await flags.evaluate(AnalyticsFilters(), context)

        assert result.newly_flagged == ("question-1",)  # 75% wrong, 4 graded responses
        assert repository.review_store.flags_snapshot()  # the review store did change
        assert repository.snapshot_fingerprint() == attempts_before


class TestApiLevelIntegrity:
    def test_no_endpoint_mutates_assessment_data(self, client, api, repository):
        before = repository.snapshot_fingerprint()
        headers = AUTH_HEADERS

        for path in (
            f"{api}/analytics/overall",
            f"{api}/analytics/courses/course-1/overall",
            f"{api}/analytics/questions",
            f"{api}/analytics/questions/question-1",
            f"{api}/analytics/questions/flagged",
            f"{api}/analytics/exports/overall.csv",
            f"{api}/analytics/exports/questions.csv",
            f"{api}/analytics/exports/flagged-questions.csv",
            f"{api}/analytics/review/actions",
            f"{api}/analytics/config",
        ):
            assert client.get(path, headers=headers).status_code == 200, path

        client.post(f"{api}/analytics/questions/flags/evaluate", headers=headers)
        client.post(
            f"{api}/analytics/review/actions",
            headers=headers,
            json={"question_id": "question-1", "action": "NO_CHANGE"},
        )

        assert repository.snapshot_fingerprint() == before

    def test_analytics_endpoints_use_safe_http_methods(self, app, api):
        """Only three of UC-10's endpoints accept an unsafe method, and none writes assessment
        data: flag evaluation and review actions write to the review store, configuration
        validation writes nothing at all.

        Scoped to UC-10's own paths. Standalone it could scan the whole document, because the
        document held nothing else; in the merged application it also holds UC-01's configuration
        PUT and UC-08's grant POST, which are other capabilities' business. Narrowing the scan is
        what keeps this a statement about analytics rather than about the application.
        """
        paths = app.openapi()["paths"]
        analytics_paths = {
            path: operations
            for path, operations in paths.items()
            if path.startswith(f"{api}/analytics")
        }
        assert analytics_paths, "the scan must actually find UC-10's endpoints"

        unsafe = {
            path
            for path, operations in analytics_paths.items()
            if {"post", "put", "patch", "delete"} & set(operations)
        }

        assert unsafe == {
            f"{api}/analytics/questions/flags/evaluate",
            f"{api}/analytics/review/actions",
            f"{api}/analytics/config/validate",
        }

    def test_no_analytics_endpoint_offers_a_delete_or_put(self, app, api):
        """UC-10 offers no PUT, PATCH or DELETE at all — not even on its own review store.

        A flag is replaced through ``POST /flags/evaluate`` and resolved through
        ``POST /review/actions``; there is deliberately no way to edit or remove either, which is
        what makes the audit trail an audit trail.
        """
        paths = app.openapi()["paths"]

        for path, operations in paths.items():
            if not path.startswith(f"{api}/analytics"):
                continue
            assert not {"put", "patch", "delete"} & set(operations), path
