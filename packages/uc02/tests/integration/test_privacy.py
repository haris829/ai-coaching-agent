"""Privacy guarantees (scope section 11).

The assembled context is private server-side data. These tests assert the
boundary rather than describing it.
"""

from __future__ import annotations

import json

from uc02.infrastructure.providers.mocks import HistoryScenario, NaricScenario
from tests.fixtures.factories import auth, make_client, make_harness, make_settings


def test_user_a_cannot_retrieve_user_b_context():
    harness = make_harness()
    client = make_client(harness)
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-a")
    )

    response = client.get("/api/v1/context/sess-1/status", headers=auth("learner-b"))
    assert response.status_code == 404
    # Indistinguishable from a session that does not exist.
    assert response.json() == client.get(
        "/api/v1/context/no-such-session/status", headers=auth("learner-b")
    ).json()


def test_a_session_id_alone_is_not_sufficient_to_retrieve_context():
    harness = make_harness()
    client = make_client(harness)
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-a")
    )
    # No identity header at all.
    assert client.get("/api/v1/context/sess-1/status").status_code == 401


def test_initialising_another_users_session_id_does_not_return_or_overwrite_context():
    harness = make_harness()
    client = make_client(harness)
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-a")
    )
    harness.reset_call_counts()

    response = client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("attacker")
    )
    assert response.status_code == 404
    assert harness.provider_call_count == 0

    # The original owner's context is untouched.
    owner = client.get("/api/v1/context/sess-1/status", headers=auth("learner-a"))
    assert owner.status_code == 200


def test_no_endpoint_returns_the_full_context_by_default():
    """Every enabled route is checked against the private fields of a context."""
    harness = make_harness(history=HistoryScenario.FEWER_THAN_20)
    client = make_client(harness)
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-a")
    )
    stored = harness.repository.peek("sess-1")  # server-side truth
    excerpt = stored.question_history.items[0].text_excerpt

    responses = [
        client.post(
            "/api/v1/context/initialize",
            json={"session_id": "sess-1"},
            headers=auth("learner-a"),
        ),
        client.get("/api/v1/context/sess-1/status", headers=auth("learner-a")),
        client.get("/api/v1/internal/context/sess-1/debug", headers=auth("learner-a")),
    ]
    for response in responses:
        blob = response.text
        assert excerpt not in blob
        assert "text_excerpt" not in blob
        assert stored.user_id not in blob


def test_the_response_never_carries_the_user_id_or_the_raw_naric_level():
    harness = make_harness(naric=NaricScenario.LEVEL_7)
    client = make_client(harness)
    body = client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-a")
    ).json()
    assert "user_id" not in body
    assert "naric" not in body
    assert "learner-a" not in json.dumps(body)


def test_the_debug_endpoint_is_disabled_by_default():
    client = make_client(make_harness())
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-a")
    )
    response = client.get("/api/v1/internal/context/sess-1/debug", headers=auth("learner-a"))
    assert response.status_code == 404


def test_the_debug_endpoint_is_disabled_under_production_configuration():
    production = make_settings(environment="production")
    assert production.debug_context_endpoint is False
    assert production.production_guard_violations() == []

    client = make_client(make_harness(settings=production))
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-a")
    )
    assert (
        client.get(
            "/api/v1/internal/context/sess-1/debug", headers=auth("learner-a")
        ).status_code
        == 404
    )


def test_enabling_the_debug_endpoint_in_production_is_reported_as_a_violation():
    unsafe = make_settings(environment="production", debug_context_endpoint=True)
    violations = unsafe.production_guard_violations()
    assert any("DEBUG_CONTEXT_ENDPOINT" in v for v in violations)


def test_the_debug_endpoint_still_enforces_ownership_when_enabled():
    settings = make_settings(debug_context_endpoint=True)
    harness = make_harness(settings=settings)
    client = make_client(harness)
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-a")
    )
    assert (
        client.get(
            "/api/v1/internal/context/sess-1/debug", headers=auth("attacker")
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/v1/internal/context/sess-1/debug", headers=auth("learner-a")
        ).status_code
        == 200
    )


def test_a_client_supplied_naric_level_is_rejected_not_absorbed():
    harness = make_harness(naric=NaricScenario.LEVEL_3)
    client = make_client(harness)
    response = client.post(
        "/api/v1/context/initialize",
        json={"session_id": "sess-1", "naric_level": 7},
        headers=auth("learner-a"),
    )
    assert response.status_code == 422


def test_the_resolved_level_always_comes_from_the_provider():
    """Even under a permissive body, the level is the provider's."""
    harness = make_harness(naric=NaricScenario.LEVEL_3)
    client = make_client(harness)
    body = client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-a")
    ).json()
    assert body["explanation_profile"]["template_id"] == "basic"
    stored = harness.repository.peek("sess-1")
    assert stored.naric.level == 3


def test_a_client_cannot_inject_context_fields():
    client = make_client(make_harness())
    for payload in (
        {"session_id": "s", "user_id": "someone-else"},
        {"session_id": "s", "explanation_profile": {"template_id": "advanced"}},
        {"session_id": "s", "source_status": {"naric": "available"}},
        {"session_id": "s", "personalization": {"available": True}},
        {"session_id": "s", "question_history": {"items": []}},
    ):
        response = client.post(
            "/api/v1/context/initialize", json=payload, headers=auth("learner-a")
        )
        assert response.status_code == 422, payload


def test_the_user_id_is_taken_from_the_resolved_identity_not_the_body():
    harness = make_harness()
    client = make_client(harness)
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("real-user")
    )
    stored = harness.repository.peek("sess-1")
    assert stored.user_id == "real-user"
    assert harness.naric.calls == ["real-user"]


def test_the_openapi_schema_exposes_no_route_returning_question_text():
    client = make_client(make_harness())
    schema = client.get("/openapi.json").json()
    initialize = schema["paths"]["/api/v1/context/initialize"]["post"]
    ref = initialize["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
    model = schema["components"]["schemas"][ref.split("/")[-1]]
    assert "user_id" not in model["properties"]
    assert "naric" not in model["properties"]
    history_ref = model["properties"]["question_history"]["$ref"].split("/")[-1]
    history_model = schema["components"]["schemas"][history_ref]
    assert set(history_model["properties"]) == {
        "count",
        "earliest_asked_at",
        "latest_asked_at",
        "truncated",
    }
