"""Error handling and response hygiene.

Two properties matter: every failure is machine-readable, and nothing internal ever
reaches the client.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.core.errors import PLATFORM_ERROR_CODES
from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.errors import ErrorCode
from tests.support.client import ApiClient, assert_error, assert_ok
from tests.support.fixtures import QUIZ_ID, seed_world


def test_every_error_uses_the_same_envelope(api: ApiClient, seeded: dict) -> None:
    error = assert_error(api.get_attempt("no-such-attempt"), 404, "ATTEMPT_NOT_FOUND")
    assert set(error) >= {"code", "message", "retryable", "requestId", "timestamp"}
    assert isinstance(error["retryable"], bool)


def test_error_codes_come_from_the_documented_taxonomy(api: ApiClient, seeded: dict) -> None:
    # UC-03 owns the codes for its domain failures; the shared kernel owns the ones for failures
    # beneath the domain (a malformed body, an unknown route). Nothing may return anything else.
    known = {str(code) for code in ErrorCode} | PLATFORM_ERROR_CODES
    responses = [
        api.get_attempt("missing"),
        api.create_attempt("no-such-quiz"),
        api.request("POST", "/api/v1/attempts", json={}),
        api.request("GET", "/api/v1/attempts", params={}),
        api.request("POST", "/api/v1/attempts", json={"quizId": QUIZ_ID}, authenticated=False),
    ]
    for response in responses:
        assert response.status_code >= 400
        assert response.json()["error"]["code"] in known


def test_request_id_is_echoed_and_correlates(api: ApiClient, seeded: dict) -> None:
    response = api.request(
        "GET", "/api/v1/attempts/missing", headers={"X-Request-Id": "trace-abc-123"}
    )
    assert response.headers["X-Request-Id"] == "trace-abc-123"
    assert response.json()["error"]["requestId"] == "trace-abc-123"


def test_a_request_id_is_generated_when_absent(api: ApiClient, seeded: dict) -> None:
    response = api.get_attempt("missing")
    assert response.headers.get("X-Request-Id")
    assert response.json()["error"]["requestId"] == response.headers["X-Request-Id"]


def test_malformed_json_is_a_request_error(api: ApiClient, seeded: dict) -> None:
    response = api.request("POST", "/api/v1/attempts", json=None, headers={"Content-Type": "application/json"})
    # An empty/invalid body is reported as a malformed request, not a crash. The platform
    # distinguishes a request that could not be understood (BAD_REQUEST) from one that was
    # understood and broke a rule (a capability's own code) — the client acts on them differently.
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"


def test_unknown_body_fields_are_rejected(api: ApiClient, seeded: dict) -> None:
    error = assert_error(
        api.request("POST", "/api/v1/attempts", json={"quizId": QUIZ_ID, "cheat": True}),
        400,
        "BAD_REQUEST",
    )
    assert error["details"]


def test_validation_issues_name_the_offending_field(api: ApiClient, seeded: dict) -> None:
    error = assert_error(api.request("POST", "/api/v1/attempts", json={}), 400, "BAD_REQUEST")
    fields = {issue["field"] for issue in error["details"]}
    assert "quizId" in fields


def test_unknown_route_returns_the_standard_envelope(api: ApiClient, seeded: dict) -> None:
    response = api.request("GET", "/api/v1/not-a-real-endpoint")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_wrong_method_returns_the_standard_envelope(api: ApiClient, seeded: dict) -> None:
    response = api.request("DELETE", "/api/v1/attempts")
    assert response.status_code == 405
    assert "error" in response.json()


def test_missing_required_query_parameter_is_reported(api: ApiClient, seeded: dict) -> None:
    assert_error(api.request("GET", "/api/v1/attempts"), 400, "BAD_REQUEST")


def test_internal_failures_do_not_leak_details(
    context: AppContext, api: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx)
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    # Force an unexpected database failure inside a request.
    from app.modules.attempt_delivery.repositories.attempt_repository import AttemptRepository

    def explode(self, *args: object, **kwargs: object) -> None:
        raise OperationalError(
            "SELECT * FROM qd_attempts WHERE secret_column = 'sensitive'",
            {},
            Exception("disk I/O error at /var/lib/uc03/data.sqlite"),
        )

    monkeypatch.setattr(AttemptRepository, "get_for_learner", explode)

    response = api.get_attempt(attempt_id)
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == str(ErrorCode.DATABASE_ERROR)
    assert body["error"]["retryable"] is True

    # No SQL, no file paths, no traceback.
    text = response.text
    assert "SELECT" not in text
    assert "secret_column" not in text
    assert "/var/lib" not in text
    assert "Traceback" not in text
    assert "OperationalError" not in text


def test_unexpected_exceptions_become_a_generic_500(
    context: AppContext, api: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx)
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    from app.modules.attempt_delivery.services.timing_service import TimingService

    def explode(self, *args: object, **kwargs: object) -> None:
        raise ZeroDivisionError("internal invariant broken in module xyz")

    monkeypatch.setattr(TimingService, "compute", explode)

    response = api.get_attempt(attempt_id)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == str(ErrorCode.INTERNAL_ERROR)
    assert "ZeroDivisionError" not in response.text
    assert "invariant" not in response.text


def test_health_endpoints_need_no_authentication(api: ApiClient) -> None:
    live = api.request("GET", "/api/health/live", authenticated=False)
    assert live.status_code == 200
    assert live.json()["status"] == "ok"

    ready = api.request("GET", "/api/health", authenticated=False)
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"


def test_openapi_schema_is_served(api: ApiClient) -> None:
    # The published schema is how the test UI discovers these endpoints. One schema now covers all
    # three capabilities, so the assertion is that UC-03's routes are *in* it — not that the whole
    # document belongs to UC-03.
    response = api.request("GET", "/api/openapi.json", authenticated=False)
    assert response.status_code == 200
    schema = response.json()
    assert "Quiz Attempt — Attempts" in {tag for path in schema["paths"].values()
                          for operation in path.values()
                          for tag in operation.get("tags", [])}
    assert "/api/v1/attempts" in schema["paths"]
    assert "/api/v1/attempts/{attempt_id}/submission" in schema["paths"]
