"""The HTTP contract (§17, §20).

Eight endpoints, the wire format a frontend would integrate against: status codes, the shared error
envelope, and the fields that tell a client what to render. No frontend is built or tested here;
what is tested is the contract one would call.

The error-envelope tests are the point of §17: every refusal is a stable ``code`` and a message a
learner could read, and never a stack trace, a driver message or an internal identifier.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.modules.identity.security import settings as identity_settings
from app.modules.retakes.container import create_container
from tests.retakes.world import (
    SequentialIdGenerator,
    build_retake_app,
    learner_auth_headers,
)

pytestmark = pytest.mark.anyio

#: The learner half joins UC-03's versioned conversation; the administrator half joins UC-01's and
#: UC-02's admin surface. The learner is no longer a path segment — it comes from the bearer token.
API = "/api/v1"
ADMIN = "/api/admin/retakes"


def _eligibility_url(quiz: str = "quiz-1") -> str:
    return f"{API}/quizzes/{quiz}/retake-eligibility"


def _retakes_url(quiz: str = "quiz-1") -> str:
    return f"{API}/quizzes/{quiz}/retakes"


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


async def test_eligibility_returns_the_state_and_the_arithmetic(
    client, first_attempt, learner_headers
):
    response = await client.get(_eligibility_url(), headers=learner_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ELIGIBLE"
    assert body["can_retake"] is True
    assert body["allowance"] == {
        "maximum_attempts": 2,
        "attempts_used": 1,
        "granted_attempts": 0,
        "total_entitlement": 2,
        "available_attempts": 1,
        "has_available_attempts": True,
        "unlimited": False,
        "relies_on_grant": False,
    }
    assert body["configuration_version_source"] == "CARRIED_FORWARD"
    assert body["guidance"] is None


async def test_exhausted_eligibility_carries_contact_guidance(
    client, quiz, attempts, learner_headers
):
    for questions in (("q1", "q2", "q3"), ("q4", "q5", "q6")):
        attempt = attempts.start_attempt(question_ids=questions)
        attempts.submit(attempt.attempt_id)

    body = (await client.get(_eligibility_url(), headers=learner_headers)).json()

    assert body["state"] == "EXHAUSTED"
    assert body["can_retake"] is False
    assert "administrator" in body["guidance"]
    assert body["blockers"][0]["code"] == "NO_ATTEMPTS_REMAINING"


async def test_an_unauthenticated_request_is_rejected(client, first_attempt):
    response = await client.get(_eligibility_url())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_a_learner_sees_only_their_own_eligibility(client, first_attempt):
    """Standalone, this asserted a 403 for editing the learner out of the path.

    That attack no longer has a surface: the learner is not in the path, so there is nothing to
    edit, and a request carries exactly one learner — the one its token resolves to. What is
    worth asserting now is that the answer follows the token rather than leaking the other
    learner's state. Alice has a submitted attempt (``first_attempt``); Bob has none, so Bob's
    eligibility is his own — no used attempt, and no retake offered.
    """
    alice = await client.get(_eligibility_url(), headers=learner_auth_headers("learner-alice"))
    bob = await client.get(_eligibility_url(), headers=learner_auth_headers("learner-bob"))

    assert alice.status_code == 200
    assert bob.status_code == 200
    assert alice.json()["allowance"]["attempts_used"] == 1
    assert bob.json()["allowance"]["attempts_used"] == 0
    assert alice.json()["learner_id"] == "learner-alice"
    assert bob.json()["learner_id"] == "learner-bob"


async def test_an_unknown_quiz_is_a_404_with_a_stable_code(client, learner_headers):
    response = await client.get(_eligibility_url(quiz="quiz-nope"), headers=learner_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "QUIZ_NOT_FOUND"


# ---------------------------------------------------------------------------
# Creating a retake
# ---------------------------------------------------------------------------


async def test_creating_a_retake_returns_201_with_the_new_attempt(
    client, first_attempt, learner_headers
):
    response = await client.post(_retakes_url(), headers=learner_headers, json={})

    assert response.status_code == 201
    body = response.json()
    assert body["replayed"] is False
    assert body["retake"]["status"] == "COMPLETED"
    assert body["retake"]["previous_attempt_id"] == first_attempt.attempt_id
    assert body["attempt"]["attempt_number"] == 2
    assert len(body["attempt"]["delivered_question_ids"]) == 3
    assert set(body["attempt"]["delivered_question_ids"]).isdisjoint({"q1", "q2", "q3"})
    assert body["question_set_difference"]["new_question_count"] == 3
    assert body["question_plan"]["exclusion_scope"] == "ALL_PREVIOUS_ATTEMPTS"


async def test_a_repeated_request_returns_200_not_a_second_attempt(
    client, first_attempt, learner_headers
):
    created = await client.post(_retakes_url(), headers=learner_headers, json={})
    replayed = await client.post(_retakes_url(), headers=learner_headers, json={})

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert replayed.json()["replayed"] is True
    assert replayed.json()["attempt"]["attempt_id"] == created.json()["attempt"]["attempt_id"]


async def test_creating_a_retake_without_a_body_works(client, first_attempt, learner_headers):
    response = await client.post(_retakes_url(), headers=learner_headers)
    assert response.status_code == 201


async def test_an_exhausted_learner_gets_409_not_a_disabled_button(
    client, quiz, attempts, learner_headers
):
    for questions in (("q1", "q2", "q3"), ("q4", "q5", "q6")):
        attempt = attempts.start_attempt(question_ids=questions)
        attempts.submit(attempt.attempt_id)

    response = await client.post(_retakes_url(), headers=learner_headers, json={})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "MAX_ATTEMPTS_REACHED"
    assert error["context"]["available_attempts"] == 0
    assert "administrator" in error["context"]["guidance"]


async def test_an_unknown_previous_attempt_is_404(client, first_attempt, learner_headers):
    response = await client.post(
        _retakes_url(), headers=learner_headers, json={"previous_attempt_id": "attempt-nope"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ATTEMPT_NOT_FOUND"


async def test_a_malformed_body_is_400_with_field_details(client, first_attempt, learner_headers):
    response = await client.post(
        _retakes_url(), headers=learner_headers, json={"previous_attempt_id": 17, "extra": True}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["details"]


async def test_an_internal_failure_never_leaks_its_cause(
    client, first_attempt, learner_headers, attempts
):
    attempts.creation_failure = RuntimeError("connection to pg-primary-3 refused: password=hunter2")

    response = await client.post(_retakes_url(), headers=learner_headers, json={})

    assert response.status_code == 502
    serialised = response.text
    assert "hunter2" not in serialised
    assert "pg-primary-3" not in serialised
    assert "Traceback" not in serialised
    assert response.json()["error"]["retryable"] is True


# ---------------------------------------------------------------------------
# Reading retakes and history
# ---------------------------------------------------------------------------


async def test_listing_and_reading_retakes(client, first_attempt, learner_headers):
    created = (await client.post(_retakes_url(), headers=learner_headers, json={})).json()
    retake_id = created["retake"]["retake_id"]

    listing = await client.get(_retakes_url(), headers=learner_headers)
    assert listing.status_code == 200
    assert [item["retake_id"] for item in listing.json()["retakes"]] == [retake_id]

    single = await client.get(
        f"{API}/retakes/{retake_id}", headers=learner_headers
    )
    assert single.status_code == 200
    assert single.json()["attempt_id"] == created["attempt"]["attempt_id"]


async def test_another_learners_retake_is_a_404_not_a_403(client, first_attempt, learner_headers):
    """A guessed id must not confirm that the record exists."""
    created = (await client.post(_retakes_url(), headers=learner_headers, json={})).json()
    retake_id = created["retake"]["retake_id"]

    response = await client.get(
        f"{API}/retakes/{retake_id}",
        headers=learner_auth_headers("learner-bob"),
    )
    assert response.status_code == 404


async def test_attempt_history_endpoint(client, first_attempt, learner_headers, scores):
    scores.record(first_attempt.attempt_id, total=1.0, maximum=3.0, percentage=33.3)
    await client.post(_retakes_url(), headers=learner_headers, json={})

    response = await client.get(
        f"{API}/quizzes/quiz-1/attempt-history", headers=learner_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["attempt_count"] == 2
    assert [entry["attempt_number"] for entry in body["entries"]] == [1, 2]
    assert body["entries"][0]["percentage"] == 33.3
    assert body["entries"][1]["is_retake"] is True


# ---------------------------------------------------------------------------
# Administrator grants
# ---------------------------------------------------------------------------


async def test_granting_requires_an_idempotency_key(client, quiz, admin_headers):
    response = await client.post(
        f"{ADMIN}/grants",
        headers=admin_headers,
        json={"learner_id": "learner-alice", "quiz_id": "quiz-1", "additional_attempts": 1},
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["details"][0]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


async def test_granting_with_a_header_key_returns_201_then_200(client, quiz, admin_headers):
    headers = {**admin_headers, "Idempotency-Key": "ticket-99"}
    payload = {"learner_id": "learner-alice", "quiz_id": "quiz-1", "additional_attempts": 1}

    created = await client.post(f"{ADMIN}/grants", headers=headers, json=payload)
    replayed = await client.post(f"{ADMIN}/grants", headers=headers, json=payload)

    assert created.status_code == 201
    assert created.json()["grant"]["granted_by"] == "admin-jo"
    assert created.json()["grant"]["course_id"] == "course-1"
    assert replayed.status_code == 200
    assert replayed.json()["replayed"] is True
    assert replayed.json()["grant"]["grant_id"] == created.json()["grant"]["grant_id"]


@pytest.mark.parametrize("size", [0, -3, 500])
async def test_an_invalid_grant_size_is_refused_at_the_schema(client, quiz, admin_headers, size):
    response = await client.post(
        f"{ADMIN}/grants",
        headers={**admin_headers, "Idempotency-Key": f"ticket-{size}"},
        json={
            "learner_id": "learner-alice",
            "quiz_id": "quiz-1",
            "additional_attempts": size,
        },
    )
    assert response.status_code == 400


async def test_the_grant_listing_shows_the_untouched_configured_maximum(
    client, quiz, admin_headers
):
    """Rendered beside the grants precisely so it is visible that granting did not change it."""
    await client.post(
        f"{ADMIN}/grants",
        headers={**admin_headers, "Idempotency-Key": "ticket-100"},
        json={"learner_id": "learner-alice", "quiz_id": "quiz-1", "additional_attempts": 2},
    )

    response = await client.get(
        f"{ADMIN}/learners/learner-alice/quizzes/quiz-1/grants", headers=admin_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["configured_maximum_attempts"] == 2
    assert body["granted_attempts"] == 2
    assert len(body["grants"]) == 1


async def test_revoking_a_grant(client, quiz, admin_headers):
    created = await client.post(
        f"{ADMIN}/grants",
        headers={**admin_headers, "Idempotency-Key": "ticket-101"},
        json={"learner_id": "learner-alice", "quiz_id": "quiz-1", "additional_attempts": 1},
    )
    grant_id = created.json()["grant"]["grant_id"]

    response = await client.post(
        f"{ADMIN}/grants/{grant_id}/revoke", headers=admin_headers, json={"reason": "In error."}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "REVOKED"
    assert response.json()["revoked_by"] == "admin-jo"


async def test_the_admin_guard_is_enforced_when_a_token_is_configured(
    monkeypatch,
    clock, configurations, bank, attempts
):
    """The same seam UC-02 uses: unset it is a no-op, set it every admin endpoint requires it."""
    configurations.publish(question_count=3, maximum_attempts=2)
    bank.add_many(6)
    # The merged administrator guard reads the *application's* settings, not the container's —
    # there is one admin seam for the whole system and UC-08 goes through it. Patched here so the
    # test exercises the real guard rather than a UC-08-local copy of one.
    monkeypatch.setattr(identity_settings, "admin_api_token", "s3cret")
    guarded = Settings(environment="test", admin_api_token="s3cret")
    container = create_container(
        settings=guarded,
        clock=clock,
        new_id=SequentialIdGenerator("uc08"),
        configurations=configurations,
        question_bank=bank,
        attempts=attempts,
    )

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=build_retake_app(container))
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        payload = {"learner_id": "learner-alice", "quiz_id": "quiz-1", "additional_attempts": 1}
        headers = {"Idempotency-Key": "ticket-102"}

        refused = await http.post(f"{ADMIN}/grants", headers=headers, json=payload)
        allowed = await http.post(
            f"{ADMIN}/grants",
            headers={**headers, "Authorization": "Bearer s3cret"},
            json=payload,
        )

    assert refused.status_code == 401
    assert allowed.status_code == 201


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def test_health_lists_retake_management_among_the_modules(client):
    """UC-08 is one capability of one application, not an application of its own.

    Standalone, this asserted ``module == "UC-08 Retake Management"`` and that a single-file test
    console was served. Neither survives the merge: there is one readiness endpoint listing every
    capability, and one React test UI. A second console would have been exactly the duplication
    the integration set out to remove.
    """
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "UC-08 Retake Management" in body["modules"]
    # The capabilities UC-08 reads from are in the same application, not behind a network hop.
    assert "UC-03 Quiz Attempt Delivery" in body["modules"]
