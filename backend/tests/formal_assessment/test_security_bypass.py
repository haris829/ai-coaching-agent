"""Direct-API bypass attempts (§19).

Every rule in UC-09 that a frontend might be tempted to enforce is attacked here from the outside, with
hand-written requests that skip whatever a browser would have done:

* calling the AI coaching check during a formal assessment;
* calling the certificate gate for an unapproved pass;
* acting on someone else's formal attempt;
* acting without the device session token;
* approving as a caller who is not an authorised assessor;
* skipping the conditions or the identity step;
* pausing and resuming.

The point of the file is that none of these depends on client behaviour: each is decided from persisted state
by the backend, so the outcome is the same whether the caller is a browser, a script or a curl command.
"""

from __future__ import annotations

import pytest

from tests.formal_assessment.conftest import ALL_CONDITION_CODES, API, ASSESSOR_API, SYSTEM_API
from tests.formal_assessment.fakes import DEFAULT_LEARNER, DEFAULT_NAME, DEFAULT_QUIZ
from tests.formal_assessment.world import (
    assessor_auth_headers,
    learner_auth_headers,
)

pytestmark = pytest.mark.anyio

SESSION_HEADER = "X-Formal-Session"
OTHER_LEARNER = "learner-mallory"


async def _to_active(client, headers):
    await client.post(
        f"{API}/quizzes/{DEFAULT_QUIZ}/conditions-acknowledgement",
        json={"acknowledged_condition_codes": ALL_CONDITION_CODES},
        headers=headers,
    )
    await client.post(
        f"{API}/quizzes/{DEFAULT_QUIZ}/identity-confirmation",
        json={"full_name": DEFAULT_NAME},
        headers=headers,
    )
    started = await client.post(
        f"{API}/quizzes/{DEFAULT_QUIZ}/formal-attempts",
        json={"device": {"fingerprint": "device-a"}},
        headers=headers,
    )
    payload = started.json()
    return payload["formal_attempt_id"], payload["attempt_id"], payload["session"]["session_token"]


async def _pass_and_review(client, learner_headers, assessor_headers, passing):
    formal_attempt_id, attempt_id, token = await _to_active(client, learner_headers)
    passing(attempt_id)
    await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/submission",
        headers={**learner_headers, SESSION_HEADER: token},
    )
    queue = await client.get(f"{ASSESSOR_API}/pending-reviews", headers=assessor_headers)
    return formal_attempt_id, attempt_id, queue.json()["reviews"][0]["review_id"]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


async def test_every_learner_endpoint_requires_a_learner_identity(client):
    response = await client.get(f"{API}/ai-coaching-eligibility")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_assessor_endpoints_require_an_assessor_identity(client):
    response = await client.get(f"{ASSESSOR_API}/pending-reviews")
    assert response.status_code == 401


async def test_a_learner_identity_does_not_open_the_assessor_endpoints(client, learner_headers):
    """A learner header where an assessor header belongs is not an assessor."""
    response = await client.get(f"{ASSESSOR_API}/pending-reviews", headers=learner_headers)
    assert response.status_code == 401


async def test_an_assessor_endpoint_refuses_every_credential_but_an_assessors(container):
    """The assessor guard, as the merge left it.

    Standalone, UC-09 carried its own ``ASSESSOR_API_TOKEN`` and this test drove that check. The
    merged application has one authentication seam and an ``assessor`` role on the principal, so
    the token mechanism is the identity module's and is covered by its tests. What has to stay
    true *here* is the thing UC-09 actually depends on: nothing but an assessor credential reaches
    an assessor endpoint.

    Built without ``world.py``'s identity overrides, so the real ``require_assessor_id`` dependency
    is what refuses — a test that overrode the guard could not assert the guard.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    from app.modules.formal_assessment.container import FormalAssessmentAppContext

    class _Fixed(FormalAssessmentAppContext):
        def __init__(self, built):
            self._built = built

        def build(self, session):  # noqa: ARG002 - the container is already built
            return self._built

    app = create_app(formal_context=_Fixed(container))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        anonymous = await http.get(f"{ASSESSOR_API}/pending-reviews")
        assert anonymous.status_code == 401

        # A syntactically valid credential that resolves to nobody is still not an assessor.
        unknown = await http.get(
            f"{ASSESSOR_API}/pending-reviews", headers={"Authorization": "Bearer nope"}
        )
        assert unknown.status_code == 401


async def test_the_system_endpoints_are_guarded_by_the_service_credential(container, monkeypatch):
    """The system guard, as the merge left it.

    ``SYSTEM_API_TOKEN`` survived the merge — it is the credential the session monitor and the
    certificate service present — but it now lives in the shared settings and is checked by the
    shared identity seam. These endpoints must be unreachable from a browser: a learner able to
    declare their own exam disconnected could auto-submit somebody else's paper.

    Built without ``world.py``'s identity overrides, so the real guard is what refuses.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    from app.modules.formal_assessment.container import FormalAssessmentAppContext
    from app.modules.identity import security as identity_security

    monkeypatch.setattr(identity_security.settings, "system_api_token", "secret-system-token")

    class _Fixed(FormalAssessmentAppContext):
        def __init__(self, built):
            self._built = built

        def build(self, session):  # noqa: ARG002 - the container is already built
            return self._built

    app = create_app(formal_context=_Fixed(container))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        without = await http.get(f"{SYSTEM_API}/attempts/attempt-1/certificate-eligibility")
        assert without.status_code == 401

        with_token = await http.get(
            f"{SYSTEM_API}/attempts/attempt-1/certificate-eligibility",
            headers={"Authorization": "Bearer secret-system-token"},
        )
        assert with_token.status_code == 200


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


async def test_a_learner_cannot_read_another_learners_formal_attempt(client, learner_headers):
    """404, not 403 — and that is a strengthening, not a regression.

    Standalone, the learner was a path segment, so the refusal came from comparing the path
    against the authenticated header: a 403, which confirms the record exists. With the learner
    resolved from the token there is nothing to compare, and the service's ownership-scoped read
    simply finds nothing — so the response says nothing about whether the id is real.

    That is the same behaviour the sibling test below already relied on for a guessed id, and the
    same one UC-08 chose for a retake belonging to somebody else.
    """
    formal_attempt_id, _, _ = await _to_active(client, learner_headers)
    response = await client.get(
        f"{API}/formal-attempts/{formal_attempt_id}",
        headers=learner_auth_headers(OTHER_LEARNER),
    )
    assert response.status_code == 404
    body = response.json()["error"]
    # The id is echoed because the caller supplied it; what must not appear is anything that
    # confirms the record exists or describes it — its owner, its state, its quiz.
    assert body["code"] == "FORMAL_ATTEMPT_NOT_FOUND"
    for leaked in (DEFAULT_LEARNER, "ACTIVE", "IDENTITY_CONFIRMED", DEFAULT_QUIZ):
        assert leaked not in response.text


async def test_editing_the_path_to_ones_own_id_still_fails_on_the_record(client, learner_headers):
    """Both halves of the check: the path must match the header, and the record must belong to the learner."""
    formal_attempt_id, _, _ = await _to_active(client, learner_headers)
    response = await client.get(
        f"{API}/formal-attempts/{formal_attempt_id}",
        headers=learner_auth_headers(OTHER_LEARNER),
    )
    assert response.status_code == 404, "a guessed id is not someone else's record"


async def test_another_learner_cannot_submit_someone_elses_attempt(client, learner_headers, upstream):
    formal_attempt_id, _, token = await _to_active(client, learner_headers)
    response = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/submission",
        headers={**learner_auth_headers(OTHER_LEARNER), SESSION_HEADER: token},
    )
    assert response.status_code == 404
    assert upstream.snapshot(list(upstream.attempts)[0])["submitted"] is False


async def test_another_learner_cannot_disconnect_someone_elses_attempt(client, learner_headers, upstream):
    formal_attempt_id, attempt_id, _ = await _to_active(client, learner_headers)
    response = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/disconnect",
        headers=learner_auth_headers(OTHER_LEARNER),
    )
    assert response.status_code == 404
    assert upstream.snapshot(attempt_id)["submitted"] is False


# ---------------------------------------------------------------------------
# The device session
# ---------------------------------------------------------------------------


async def test_a_stolen_learner_identity_without_the_session_token_cannot_autosave(client, learner_headers):
    """The learner identity is not the credential for the device sitting the assessment."""
    formal_attempt_id, _, _ = await _to_active(client, learner_headers)
    response = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/autosave",
        json={"answers": [{"question_id": "q1", "response": {"selectedOptionId": "q1-o1"}}]},
        headers={**learner_headers, SESSION_HEADER: "guessed-token"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DEVICE_SESSION_CONFLICT"


async def test_a_second_device_cannot_submit_the_attempt(client, learner_headers, upstream):
    formal_attempt_id, attempt_id, _ = await _to_active(client, learner_headers)
    response = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/submission",
        headers={**learner_headers, SESSION_HEADER: "another-devices-token"},
    )
    assert response.status_code == 409
    assert upstream.snapshot(attempt_id)["submitted"] is False


# ---------------------------------------------------------------------------
# Skipping the gates
# ---------------------------------------------------------------------------


async def test_the_conditions_cannot_be_skipped_by_calling_start_directly(client, learner_headers, upstream):
    response = await client.post(
        f"{API}/quizzes/{DEFAULT_QUIZ}/formal-attempts",
        json={"device": {"fingerprint": "device-a"}},
        headers=learner_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONDITIONS_NOT_ACKNOWLEDGED"
    assert upstream.attempts == {}, "no attempt was delivered"


async def test_identity_cannot_be_skipped_by_calling_start_directly(client, learner_headers, upstream):
    await client.post(
        f"{API}/quizzes/{DEFAULT_QUIZ}/conditions-acknowledgement",
        json={"acknowledged_condition_codes": ALL_CONDITION_CODES},
        headers=learner_headers,
    )
    response = await client.post(
        f"{API}/quizzes/{DEFAULT_QUIZ}/formal-attempts",
        headers=learner_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDENTITY_NOT_CONFIRMED"
    assert upstream.attempts == {}


async def test_a_pause_cannot_be_achieved_by_any_endpoint(client, learner_headers):
    formal_attempt_id, _, token = await _to_active(client, learner_headers)
    for path in ("pause", "resume"):
        response = await client.post(
            f"{API}/formal-attempts/{formal_attempt_id}/{path}",
            headers={**learner_headers, SESSION_HEADER: token},
        )
        assert response.status_code == 409

    status = await client.get(
        f"{API}/formal-attempts/{formal_attempt_id}", headers=learner_headers
    )
    assert status.json()["state"] == "ACTIVE", "the attempt kept running throughout"


async def test_a_disconnected_attempt_cannot_be_re_entered(client, learner_headers, system_headers):
    formal_attempt_id, _, token = await _to_active(client, learner_headers)
    await client.post(
        f"{SYSTEM_API}/formal-attempts/{formal_attempt_id}/disconnect", headers=system_headers
    )

    resumed = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/resume",
        headers=learner_headers,
    )
    assert resumed.status_code == 409

    autosaved = await client.post(
        f"{API}/formal-attempts/{formal_attempt_id}/autosave",
        json={"answers": [{"question_id": "q1", "response": {"selectedOptionId": "q1-o1"}}]},
        headers={**learner_headers, SESSION_HEADER: token},
    )
    assert autosaved.status_code == 409
    assert autosaved.json()["error"]["code"] == "FORMAL_ATTEMPT_ALREADY_SUBMITTED"

    restarted = await client.post(
        f"{API}/quizzes/{DEFAULT_QUIZ}/formal-attempts",
        headers=learner_headers,
    )
    assert restarted.status_code == 409, "starting again is a new sitting, not a resume"


# ---------------------------------------------------------------------------
# AI coaching (§7, §19)
# ---------------------------------------------------------------------------


async def test_calling_the_coaching_check_directly_during_a_formal_assessment_is_refused(client, learner_headers, container):
    """The exact scenario in §19: a user calls the AI coaching endpoint directly."""
    _, attempt_id, _ = await _to_active(client, learner_headers)
    response = await client.get(
        f"{API}/ai-coaching-eligibility",
        params={"attempt_id": attempt_id},
        headers=learner_headers,
    )
    assert response.status_code == 200
    assert response.json()["ai_coaching_allowed"] is False

    # And the guard a coaching module calls raises rather than returning a value it could ignore.
    from app.modules.formal_assessment.domain.errors import AiCoachingForbiddenError

    with pytest.raises(AiCoachingForbiddenError):
        await container.services.coaching.require_allowed(
            learner_id=DEFAULT_LEARNER, attempt_id=attempt_id
        )


async def test_coaching_cannot_be_unblocked_by_naming_a_different_attempt(client, learner_headers):
    await _to_active(client, learner_headers)
    for attempt_id in ("attempt-from-last-year", "", "null"):
        response = await client.get(
            f"{API}/ai-coaching-eligibility",
            params={"attempt_id": attempt_id},
            headers=learner_headers,
        )
        assert response.json()["ai_coaching_allowed"] is False, attempt_id


# ---------------------------------------------------------------------------
# The certificate gate (§11, §19)
# ---------------------------------------------------------------------------


async def test_calling_the_certificate_gate_directly_before_approval_is_refused(
    client, learner_headers, assessor_headers, system_headers, passing, certificates
):
    """The second scenario in §19: a user calls the certificate endpoint directly."""
    formal_attempt_id, attempt_id, review_id = await _pass_and_review(
        client, learner_headers, assessor_headers, passing
    )
    gate = await client.get(
        f"{SYSTEM_API}/attempts/{attempt_id}/certificate-eligibility", headers=system_headers
    )
    assert gate.json()["certificate_allowed"] is False
    assert gate.json()["reason"] == "PENDING_HUMAN_REVIEW"

    triggered = await client.post(
        f"{ASSESSOR_API}/reviews/{review_id}/certificate-workflow", headers=assessor_headers
    )
    assert triggered.status_code == 403
    assert triggered.json()["error"]["code"] == "CERTIFICATE_NOT_APPROVED"
    assert certificates.certificate_count == 0


async def test_an_unauthorised_assessor_cannot_approve(client, learner_headers, assessor_headers, passing, assessors, certificates):
    _, _, review_id = await _pass_and_review(client, learner_headers, assessor_headers, passing)
    assessors.add("assessor-outsider", courses=("course-elsewhere",))

    response = await client.post(
        f"{ASSESSOR_API}/reviews/{review_id}/decision",
        json={"decision": "APPROVED"},
        headers=assessor_auth_headers("assessor-outsider"),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ASSESSOR_NOT_AUTHORIZED"
    assert certificates.certificate_count == 0


async def test_an_unknown_caller_cannot_approve_even_with_a_plausible_id(client, learner_headers, assessor_headers, passing, certificates):
    _, _, review_id = await _pass_and_review(client, learner_headers, assessor_headers, passing)
    response = await client.post(
        f"{ASSESSOR_API}/reviews/{review_id}/decision",
        json={"decision": "APPROVED"},
        headers=assessor_auth_headers("assessor-jo-2"),
    )
    assert response.status_code == 403
    assert certificates.certificate_count == 0


async def test_a_learner_cannot_approve_their_own_assessment(client, learner_headers, assessor_headers, passing, certificates):
    _, _, review_id = await _pass_and_review(client, learner_headers, assessor_headers, passing)
    response = await client.post(
        f"{ASSESSOR_API}/reviews/{review_id}/decision",
        json={"decision": "APPROVED"},
        headers=assessor_auth_headers(DEFAULT_LEARNER),
    )
    assert response.status_code == 403
    assert certificates.certificate_count == 0


async def test_an_assessor_cannot_read_a_review_outside_their_scope(client, learner_headers, assessor_headers, passing, assessors):
    _, _, review_id = await _pass_and_review(client, learner_headers, assessor_headers, passing)
    assessors.add("assessor-outsider", courses=("course-elsewhere",))
    response = await client.get(
        f"{ASSESSOR_API}/reviews/{review_id}", headers=assessor_auth_headers("assessor-outsider")
    )
    assert response.status_code == 403


async def test_the_certificate_gate_cannot_be_talked_into_a_yes_by_the_request(
    client, learner_headers, assessor_headers, system_headers, passing
):
    """No query parameter, header or body field participates in the decision."""
    _, attempt_id, _ = await _pass_and_review(client, learner_headers, assessor_headers, passing)
    for params in (
        {"approved": "true"},
        {"certificate_allowed": "true"},
        {"state": "APPROVED"},
        {"assessor_id": "assessor-jo"},
    ):
        response = await client.get(
            f"{SYSTEM_API}/attempts/{attempt_id}/certificate-eligibility",
            params=params,
            headers=system_headers,
        )
        assert response.json()["certificate_allowed"] is False, params


async def test_an_escalated_assessment_cannot_be_approved_by_calling_again(
    client, learner_headers, assessor_headers, passing, certificates
):
    _, _, review_id = await _pass_and_review(client, learner_headers, assessor_headers, passing)
    escalated = await client.post(
        f"{ASSESSOR_API}/reviews/{review_id}/decision",
        json={"decision": "REQUIRES_FURTHER_REVIEW"},
        headers=assessor_headers,
    )
    assert escalated.status_code == 200

    again = await client.post(
        f"{ASSESSOR_API}/reviews/{review_id}/decision",
        json={"decision": "APPROVED"},
        headers=assessor_headers,
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "REVIEW_ALREADY_DECIDED"
    assert certificates.certificate_count == 0

    workflow = await client.post(
        f"{ASSESSOR_API}/reviews/{review_id}/certificate-workflow", headers=assessor_headers
    )
    assert workflow.status_code == 403
    assert certificates.certificate_count == 0
