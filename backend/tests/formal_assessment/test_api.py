"""HTTP contract tests (§17, §18).

The full learner → assessor → certificate journey over HTTP, plus the status codes and error envelope a client
will branch on. These are the tests that prove the API contract the company's frontend will integrate against
actually behaves as the documentation says.
"""

from __future__ import annotations

import pytest

from tests.formal_assessment.conftest import ALL_CONDITION_CODES, API, ASSESSOR_API, SYSTEM_API
from tests.formal_assessment.fakes import (
    CLIENT_REQUEST_ID,
    DEFAULT_NAME,
    DEFAULT_QUIZ,
)

pytestmark = pytest.mark.anyio

SESSION_HEADER = "X-Formal-Session"


async def _acknowledge(client, headers, quiz_id: str = DEFAULT_QUIZ):
    return await client.post(
        f"{API}/quizzes/{quiz_id}/conditions-acknowledgement",
        json={"acknowledged_condition_codes": ALL_CONDITION_CODES},
        headers=headers,
    )


async def _confirm(client, headers, name: str = DEFAULT_NAME, email: str | None = None):
    body: dict[str, object] = {"full_name": name}
    if email is not None:
        body["email"] = email
    return await client.post(
        f"{API}/quizzes/{DEFAULT_QUIZ}/identity-confirmation",
        json=body,
        headers=headers,
    )


async def _start(client, headers, **body):
    return await client.post(
        f"{API}/quizzes/{DEFAULT_QUIZ}/formal-attempts",
        json=body or None,
        headers=headers,
    )


async def _to_active(client, headers) -> tuple[str, str, str]:
    await _acknowledge(client, headers)
    await _confirm(client, headers)
    started = await _start(client, headers, device={"fingerprint": "device-a"})
    assert started.status_code == 201
    payload = started.json()
    return (
        payload["formal_attempt_id"],
        payload["attempt_id"],
        payload["session"]["session_token"],
    )


# ---------------------------------------------------------------------------
# Health and conditions
# ---------------------------------------------------------------------------


async def test_health_lists_formal_assessment_among_the_modules(client):
    """UC-09 is one capability of one application, not an application of its own.

    Standalone this asserted ``module == "UC-09 Formal Assessment Mode"`` and, pointedly, that the
    payload carried no ``database`` key at all. Both were true of a module that owned no storage.
    The merged application has one readiness endpoint listing every capability, and it does report
    the database — because there is one, shared by all of them.
    """
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert "UC-09 Formal Assessment Mode" in body["modules"]
    # The capabilities UC-09 supervises and defers to are in the same application.
    assert "UC-03 Quiz Attempt Delivery" in body["modules"]
    assert "UC-05 Pass/Fail & Certificate Gating" in body["modules"]


async def test_the_conditions_endpoint_returns_all_seven(client, learner_headers):
    response = await client.get(f"{API}/quizzes/{DEFAULT_QUIZ}/formal-conditions", headers=learner_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body["conditions"]) == 7
    assert body["is_formal_assessment"] is True
    assert body["requires_assessor_approval"] is True
    assert len(body["required_condition_codes"]) == 7


async def test_an_unknown_quiz_returns_the_standard_error_envelope(client, learner_headers):
    response = await client.get(f"{API}/quizzes/quiz-nope/formal-conditions", headers=learner_headers)
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "QUIZ_NOT_FOUND"
    assert "message" in error
    assert error["retryable"] is False


# ---------------------------------------------------------------------------
# Acknowledgement and identity
# ---------------------------------------------------------------------------


async def test_acknowledging_returns_201_then_200(client, learner_headers):
    first = await _acknowledge(client, learner_headers)
    assert first.status_code == 201
    assert first.json()["created"] is True
    assert first.json()["conditions_acknowledged"] is True

    second = await _acknowledge(client, learner_headers)
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["formal_attempt_id"] == first.json()["formal_attempt_id"]


async def test_an_incomplete_acknowledgement_is_422(client, learner_headers):
    response = await client.post(
        f"{API}/quizzes/{DEFAULT_QUIZ}/conditions-acknowledgement",
        json={"acknowledged_condition_codes": ALL_CONDITION_CODES[:2]},
        headers=learner_headers,
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "CONDITIONS_ACKNOWLEDGEMENT_INCOMPLETE"
    assert len(error["context"]["missing_conditions"]) == 5


async def test_a_conditions_acknowledged_boolean_cannot_be_sent(client, learner_headers):
    """The backend derives the flag; there is no field a client could use to claim it."""
    response = await client.post(
        f"{API}/quizzes/{DEFAULT_QUIZ}/conditions-acknowledgement",
        json={"acknowledged_condition_codes": ALL_CONDITION_CODES, "conditions_acknowledged": True},
        headers=learner_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"


async def test_confirming_identity_over_http(client, learner_headers):
    await _acknowledge(client, learner_headers)
    response = await _confirm(client, learner_headers, email="john.smith@example.com")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "IDENTITY_CONFIRMED"
    assert body["identity_check"]["confirmed"] is True
    assert "exact" in body["identity_check"]["name_match_rule"].lower()


async def test_a_name_mismatch_is_422_with_the_failing_field(client, learner_headers):
    await _acknowledge(client, learner_headers)
    response = await _confirm(client, learner_headers, name="john smith")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "IDENTITY_MISMATCH"
    assert error["context"]["mismatched_fields"] == ["FULL_NAME"]
    assert DEFAULT_NAME not in response.text, "the expected value is never echoed"


async def test_an_unconfirmed_email_is_409(client, learner_headers, profiles):
    profiles.unconfirm_email()
    await _acknowledge(client, learner_headers)
    response = await _confirm(client, learner_headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_NOT_CONFIRMED"


# ---------------------------------------------------------------------------
# Starting, autosaving, submitting
# ---------------------------------------------------------------------------


async def test_starting_returns_the_session_token_once(client, learner_headers):
    formal_attempt_id, attempt_id, token = await _to_active(client, learner_headers)
    assert token
    assert attempt_id

    status = await client.get(
        f"{API}/formal-attempts/{formal_attempt_id}", headers=learner_headers
    )
    assert status.status_code == 200
    assert token not in status.text, "no read endpoint hands the token out again"


async def test_starting_without_acknowledging_is_409(client, learner_headers):
    response = await _start(client, learner_headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONDITIONS_NOT_ACKNOWLEDGED"


async def test_starting_without_identity_is_409(client, learner_headers):
    await _acknowledge(client, learner_headers)
    response = await _start(client, learner_headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDENTITY_NOT_CONFIRMED"


async def test_a_second_device_start_is_409(client, learner_headers):
    await _to_active(client, learner_headers)
    response = await _start(client, learner_headers, device={"fingerprint": "device-b"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SECOND_DEVICE_REJECTED"


async def test_a_replayed_start_returns_200_and_the_same_session(client, learner_headers):
    await _acknowledge(client, learner_headers)
    await _confirm(client, learner_headers)
    first = await _start(client, learner_headers, client_request_id=CLIENT_REQUEST_ID)
    assert first.status_code == 201
    second = await _start(client, learner_headers, client_request_id=CLIENT_REQUEST_ID)
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["session"]["session_id"] == first.json()["session"]["session_id"]


async def test_the_status_endpoint_states_what_is_not_allowed(client, learner_headers):
    formal_attempt_id, _, _ = await _to_active(client, learner_headers)
    response = await client.get(
        f"{API}/formal-attempts/{formal_attempt_id}", headers=learner_headers
    )
    body = response.json()
    assert body["pause_allowed"] is False
    assert body["resume_allowed"] is False
    assert body["ai_coaching_allowed"] is False
    assert body["autosaved_state"] is not None


async def test_the_open_attempt_endpoint_helps_a_client_reload_before_starting(client, learner_headers):
    empty = await client.get(
        f"{API}/quizzes/{DEFAULT_QUIZ}/formal-attempts/open",
        headers=learner_headers,
    )
    assert empty.status_code == 200
    assert empty.json() is None

    await _acknowledge(client, learner_headers)
    present = await client.get(
        f"{API}/quizzes/{DEFAULT_QUIZ}/formal-attempts/open",
        headers=learner_headers,
    )
    assert present.json()["state"] == "CONDITIONS_ACKNOWLEDGED"


async def test_autosave_requires_the_session_header(client, learner_headers):
    formal_attempt_id, _, token = await _to_active(client, learner_headers)
    body = {"answers": [{"question_id": "q1", "response": {"selectedOptionId": "q1-o1"}}]}

    without = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/autosave",
        json=body,
        headers=learner_headers,
    )
    assert without.status_code == 409
    assert without.json()["error"]["code"] == "DEVICE_SESSION_CONFLICT"

    with_token = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/autosave",
        json=body,
        headers={**learner_headers, SESSION_HEADER: token},
    )
    assert with_token.status_code == 200
    assert with_token.json()["saved_count"] == 1
    assert with_token.json()["answered_questions"] == 1


async def test_the_heartbeat_publishes_the_timeout(client, learner_headers):
    formal_attempt_id, _, token = await _to_active(client, learner_headers)
    response = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/session/heartbeat",
        headers={**learner_headers, SESSION_HEADER: token},
    )
    assert response.status_code == 200
    assert response.json()["heartbeat_timeout_seconds"] == 90
    assert response.json()["state"] == "ACTIVE"


async def test_pause_is_always_409(client, learner_headers):
    formal_attempt_id, _, _ = await _to_active(client, learner_headers)
    response = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/pause",
        headers=learner_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PAUSE_NOT_ALLOWED"


async def test_resume_is_always_409(client, learner_headers):
    formal_attempt_id, _, _ = await _to_active(client, learner_headers)
    response = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/resume",
        headers=learner_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RESUME_NOT_ALLOWED"


async def test_submitting_returns_200_and_a_duplicate_is_a_replay(client, learner_headers, upstream):
    """200 either way, as in UC-03 where only attempt creation is a 201; ``replayed`` tells them apart."""
    formal_attempt_id, _, token = await _to_active(client, learner_headers)
    headers = {**learner_headers, SESSION_HEADER: token}

    first = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/submission",
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["replayed"] is False

    second = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/submission",
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert len(upstream.submissions) == 1


async def test_the_learner_can_report_a_disconnect(client, learner_headers, upstream):
    formal_attempt_id, attempt_id, token = await _to_active(client, learner_headers)
    await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/autosave",
        json={"answers": [{"question_id": "q1", "response": {"selectedOptionId": "q1-o1"}}]},
        headers={**learner_headers, SESSION_HEADER: token},
    )
    response = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/disconnect",
        json={"reason": "BROWSER_UNLOAD"},
        headers=learner_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["auto_submitted"] is True
    assert body["submission_reason"] == "DISCONNECT_AUTO_SUBMIT"
    assert body["disconnect"]["answered_questions"] == 1
    assert upstream.snapshot(attempt_id)["submitted_answers"] == 1


# ---------------------------------------------------------------------------
# AI coaching and the certificate gate
# ---------------------------------------------------------------------------


async def test_the_coaching_eligibility_endpoint(client, learner_headers):
    allowed = await client.get(
        f"{API}/ai-coaching-eligibility", headers=learner_headers
    )
    assert allowed.json()["ai_coaching_allowed"] is True

    formal_attempt_id, attempt_id, _ = await _to_active(client, learner_headers)
    blocked = await client.get(
        f"{API}/ai-coaching-eligibility",
        params={"attempt_id": attempt_id},
        headers=learner_headers,
    )
    assert blocked.json()["ai_coaching_allowed"] is False
    assert blocked.json()["reason"] == "FORMAL_ATTEMPT_IN_PROGRESS"
    assert blocked.json()["formal_attempt_id"] == formal_attempt_id


async def test_the_certificate_gate_endpoint_blocks_a_pending_review(
    client, learner_headers, system_headers, passing, upstream
):
    formal_attempt_id, attempt_id, token = await _to_active(client, learner_headers)
    passing(attempt_id)
    await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/submission",
        headers={**learner_headers, SESSION_HEADER: token},
    )

    gate = await client.get(f"{SYSTEM_API}/attempts/{attempt_id}/certificate-eligibility", headers=system_headers)
    assert gate.status_code == 200
    body = gate.json()
    assert body["certificate_allowed"] is False
    assert body["decision"] == "BLOCKED"
    assert body["reason"] == "PENDING_HUMAN_REVIEW"

    learner_view = await client.get(
        f"{API}/formal-attempts/{formal_attempt_id}/certificate-eligibility",
        headers=learner_headers,
    )
    assert learner_view.json()["certificate_allowed"] is False


async def test_an_ordinary_attempt_gets_no_opinion(client, system_headers):
    gate = await client.get(
        f"{SYSTEM_API}/attempts/attempt-ordinary/certificate-eligibility", headers=system_headers
    )
    assert gate.json()["decision"] == "NOT_FORMAL_ASSESSMENT"
    assert gate.json()["formal_assessment"] is False


# ---------------------------------------------------------------------------
# The assessor journey
# ---------------------------------------------------------------------------


async def test_the_full_journey_from_pass_to_certificate(
    client, learner_headers, assessor_headers, system_headers, passing, certificates
):
    formal_attempt_id, attempt_id, token = await _to_active(client, learner_headers)
    passing(attempt_id)
    await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/submission",
        headers={**learner_headers, SESSION_HEADER: token},
    )

    queue = await client.get(f"{ASSESSOR_API}/pending-reviews", headers=assessor_headers)
    assert queue.status_code == 200
    assert queue.json()["total_pending"] == 1
    review_id = queue.json()["reviews"][0]["review_id"]

    detail = await client.get(f"{ASSESSOR_API}/reviews/{review_id}", headers=assessor_headers)
    assert detail.status_code == 200
    assert detail.json()["learner"]["full_name"] == DEFAULT_NAME
    assert len(detail.json()["responses"]) == 3

    started = await client.post(
        f"{ASSESSOR_API}/reviews/{review_id}/review-start", headers=assessor_headers
    )
    assert started.json()["state"] == "IN_REVIEW"

    decision = await client.post(
        f"{ASSESSOR_API}/reviews/{review_id}/decision",
        json={"decision": "APPROVED", "notes": "Verified."},
        headers=assessor_headers,
    )
    assert decision.status_code == 200
    body = decision.json()
    assert body["review"]["state"] == "APPROVED"
    assert body["formal_attempt"]["state"] == "CERTIFICATE_ALLOWED"
    assert body["formal_attempt"]["certificate_allowed"] is True
    assert body["notification_delivered"] is True
    assert certificates.certificate_count == 1

    gate = await client.get(
        f"{SYSTEM_API}/attempts/{attempt_id}/certificate-eligibility", headers=system_headers
    )
    assert gate.json()["certificate_allowed"] is True
    assert gate.json()["approved_by"] == "assessor-jo"


async def test_an_invalid_decision_is_422(client, learner_headers, assessor_headers, passing):
    formal_attempt_id, attempt_id, token = await _to_active(client, learner_headers)
    passing(attempt_id)
    await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/submission",
        headers={**learner_headers, SESSION_HEADER: token},
    )
    review_id = (await client.get(f"{ASSESSOR_API}/pending-reviews", headers=assessor_headers)).json()[
        "reviews"
    ][0]["review_id"]

    response = await client.post(
        f"{ASSESSOR_API}/reviews/{review_id}/decision",
        json={"decision": "MAYBE"},
        headers=assessor_headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REVIEW_DECISION"


async def test_the_system_endpoints_drive_disconnect_and_resolution(
    client, learner_headers, system_headers, passing
):
    formal_attempt_id, attempt_id, _ = await _to_active(client, learner_headers)

    disconnected = await client.post(
        f"{SYSTEM_API}/formal-attempts/{formal_attempt_id}/disconnect",
        json={"last_seen_at": "2026-03-01T09:02:00.000Z", "reason": "HEARTBEAT_TIMEOUT"},
        headers=system_headers,
    )
    assert disconnected.status_code == 200
    assert disconnected.json()["state"] == "SUBMITTED"

    passing(attempt_id)
    resolved = await client.post(
        f"{SYSTEM_API}/formal-attempts/{formal_attempt_id}/result-resolution", headers=system_headers
    )
    assert resolved.status_code == 200
    assert resolved.json()["resolution_outcome"] == "RESOLVED"
    assert resolved.json()["state"] == "PENDING_REVIEW"


async def test_the_recovery_endpoints_expose_the_queue_backlog(
    client, learner_headers, system_headers, passing, queue
):
    formal_attempt_id, attempt_id, token = await _to_active(client, learner_headers)
    passing(attempt_id)
    queue.unavailable = True
    await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/submission",
        headers={**learner_headers, SESSION_HEADER: token},
    )

    listed = await client.get(f"{SYSTEM_API}/review-queue/unpublished", headers=system_headers)
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    review_id = listed.json()["reviews"][0]["review_id"]

    failed_retry = await client.post(
        f"{SYSTEM_API}/review-queue/{review_id}/retry", headers=system_headers
    )
    assert failed_retry.status_code == 503
    assert failed_retry.json()["error"]["code"] == "REVIEW_QUEUE_UNAVAILABLE"
    assert failed_retry.json()["error"]["retryable"] is True

    queue.unavailable = False
    sweep = await client.post(f"{SYSTEM_API}/review-queue/retry", headers=system_headers)
    assert sweep.status_code == 200
    assert sweep.json()["published"] == 1

    empty = await client.get(f"{SYSTEM_API}/review-queue/unpublished", headers=system_headers)
    assert empty.json()["count"] == 0


async def test_the_openapi_document_describes_every_endpoint(client):
    """All 25 endpoints, counted across the three roots the merge mounted them under.

    Standalone they shared one ``/formal-assessments`` prefix and could be counted by substring.
    Here the learner half sits under ``/api/v1`` with UC-03's, the assessor half under
    ``/api/assessor`` and the system half under ``/api/system/formal-assessments``, because the
    three carry three different credentials — so the count is the sum of the three groups.
    """
    response = await client.get("/api/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    learner = [
        path
        for path in paths
        if path.startswith("/api/v1/")
        and (
            "formal" in path
            or "conditions-acknowledgement" in path
            or "identity-confirmation" in path
            or "ai-coaching-eligibility" in path
        )
    ]
    assessor = [path for path in paths if path.startswith("/api/assessor/")]
    system = [path for path in paths if path.startswith("/api/system/formal-assessments/")]

    assert len(learner) == 14, learner
    assert len(assessor) == 5, assessor
    assert len(system) == 6, system
    assert len(learner) + len(assessor) + len(system) == 25
