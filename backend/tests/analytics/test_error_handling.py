"""Error handling tests (spec sections 24, 25).

Two things are being checked: that each failure mode produces the right typed
error, and that nothing internal leaks into what a client can see.
"""

from __future__ import annotations

import pytest

from app.modules.analytics.cancellation import QueryContext
from app.modules.analytics.domain.enums import ReviewActionType
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.domain.review import ReviewActionRequest
from app.modules.analytics.errors import (
    AnalyticsError,
    DatasetTooLargeError,
    QueryTimeoutError,
    RepositoryUnavailableError,
    UpstreamDataInvalidError,
)
from app.modules.analytics.services import AnalyticsService, ReviewService

from .conftest import ADMIN_ID, make_settings
from .doubles import (
    ContractViolatingRepository,
    FailingAnalyticsRepository,
    FailingReviewRepository,
    HangingAnalyticsRepository,
)

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio


class TestRepositoryFailures:
    @pytest.fixture
    def failing_service(self, settings, clock) -> AnalyticsService:
        return AnalyticsService(FailingAnalyticsRepository(), settings, clock)

    async def test_overall_analytics_reports_the_provider_as_unavailable(
        self, failing_service, context
    ):
        with pytest.raises(RepositoryUnavailableError) as exc:
            await failing_service.get_overall_analytics(AnalyticsFilters(), context)

        assert exc.value.code == "DATA_PROVIDER_UNAVAILABLE"
        assert exc.value.http_status == 503

    async def test_question_analytics_reports_the_provider_as_unavailable(
        self, failing_service, context
    ):
        with pytest.raises(RepositoryUnavailableError):
            await failing_service.aggregate_question_analytics(AnalyticsFilters(), context)

    async def test_driver_details_never_reach_the_client(self, failing_service, context):
        with pytest.raises(RepositoryUnavailableError) as exc:
            await failing_service.get_overall_analytics(AnalyticsFilters(), context)

        public = exc.value.public_message()
        payload = str(exc.value.to_response())
        for secret in ("psycopg2", "db-prod-01", "user analytics", "OperationalError"):
            assert secret not in public
            assert secret not in payload

    async def test_underlying_cause_is_preserved_for_server_side_diagnosis(
        self, failing_service, context
    ):
        with pytest.raises(RepositoryUnavailableError) as exc:
            await failing_service.get_overall_analytics(AnalyticsFilters(), context)

        assert isinstance(exc.value.__cause__, ConnectionResetError)

    async def test_health_check_reports_degraded_rather_than_raising(
        self, failing_service, context
    ):
        assert await failing_service.check_provider_health(context) is False

    async def test_review_store_failure_is_typed(self, settings, clock, context):
        service = ReviewService(FailingReviewRepository(), settings, clock)

        with pytest.raises(RepositoryUnavailableError) as exc:
            await service.record_action(
                ReviewActionRequest(question_id="q1", action=ReviewActionType.NO_CHANGE),
                ADMIN_ID,
                context,
            )

        assert exc.value.http_status == 503
        assert "timed out" not in exc.value.public_message()


class TestTimeouts:
    async def test_service_enforces_the_deadline_on_an_uncooperative_provider(
        self, settings, clock
    ):
        service = AnalyticsService(HangingAnalyticsRepository(delay=30), settings, clock)
        context = QueryContext.create(timeout_seconds=0.05)  # real clock: must expire

        with pytest.raises(QueryTimeoutError) as exc:
            await service.get_overall_analytics(AnalyticsFilters(), context)

        assert exc.value.code == "QUERY_TIMEOUT"
        assert exc.value.http_status == 504

    async def test_timeout_message_tells_the_caller_what_to_do(self, settings, clock):
        service = AnalyticsService(HangingAnalyticsRepository(delay=30), settings, clock)
        context = QueryContext.create(timeout_seconds=0.05)

        with pytest.raises(QueryTimeoutError) as exc:
            await service.get_overall_analytics(AnalyticsFilters(), context)

        assert "Narrow the date range" in exc.value.message


class TestInvalidData:
    async def test_contract_violation_is_reported_as_an_upstream_data_fault(
        self, settings, clock, context
    ):
        service = AnalyticsService(ContractViolatingRepository(), settings, clock)

        with pytest.raises(UpstreamDataInvalidError) as exc:
            await service.get_overall_analytics(AnalyticsFilters(), context)

        # 502, not 503: retrying will not fix a score on the wrong scale.
        assert exc.value.code == "UPSTREAM_DATA_INVALID"
        assert exc.value.http_status == 502
        assert "score" in exc.value.details["fields"]

    async def test_contract_violation_does_not_leak_learner_values(
        self, settings, clock, context
    ):
        service = AnalyticsService(ContractViolatingRepository(), settings, clock)

        with pytest.raises(UpstreamDataInvalidError) as exc:
            await service.get_overall_analytics(AnalyticsFilters(), context)

        payload = str(exc.value.to_response())
        assert "880" not in payload
        assert "learner-1" not in payload


class TestScanLimits:
    async def test_oversized_scope_is_refused_before_scanning(
        self, repository, clock, context
    ):
        settings = make_settings(max_scanned_records=2)
        service = AnalyticsService(repository, settings, clock)

        with pytest.raises(DatasetTooLargeError) as exc:
            await service.get_overall_analytics(AnalyticsFilters(), context)

        assert exc.value.http_status == 422
        assert exc.value.details["matched"] == 5
        assert exc.value.details["max_scanned_records"] == 2

    async def test_oversized_response_scan_is_refused(self, repository, clock, context):
        settings = make_settings(max_scanned_records=3)
        service = AnalyticsService(repository, settings, clock)

        with pytest.raises(DatasetTooLargeError):
            await service.aggregate_question_analytics(AnalyticsFilters(), context)

    async def test_within_the_limit_nothing_is_refused(self, repository, clock, context):
        settings = make_settings(max_scanned_records=1000)
        service = AnalyticsService(repository, settings, clock)

        result = await service.get_overall_analytics(AnalyticsFilters(), context)

        assert result.attempt_volume == 5


class TestErrorEnvelope:
    def test_every_error_declares_a_code_and_status(self):
        subclasses = _all_subclasses(AnalyticsError)

        assert subclasses
        for error_class in subclasses:
            assert error_class.code != AnalyticsError.code or error_class is AnalyticsError
            assert 400 <= error_class.http_status <= 599

    def test_codes_are_unique(self):
        codes = [cls.code for cls in _all_subclasses(AnalyticsError)]

        assert len(codes) == len(set(codes)), "duplicate error codes make client branching unsafe"

    def test_payload_shape_is_stable(self):
        payload = RepositoryUnavailableError("internal detail").to_response(request_id="req-1")

        assert set(payload) == {"error"}
        # The merged envelope: camelCase, and it carries ``retryable`` and ``timestamp`` because
        # every capability's refusals go through one renderer and a client branches on both.
        assert {"code", "message", "retryable", "requestId", "timestamp"} <= set(payload["error"])

    def test_payload_never_contains_a_traceback(self):
        try:
            raise ValueError("inner failure at line 42")
        except ValueError as inner:
            error = RepositoryUnavailableError("wrapped", cause=inner)

        payload = str(error.to_response())

        assert "Traceback" not in payload
        assert "line 42" not in payload


def _all_subclasses(root: type) -> list[type]:
    found: list[type] = []
    for subclass in root.__subclasses__():
        found.append(subclass)
        found.extend(_all_subclasses(subclass))
    # InvalidThresholdError deliberately refines ConfigurationError; both are kept.
    return found
