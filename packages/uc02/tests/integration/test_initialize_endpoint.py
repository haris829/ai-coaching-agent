"""POST /api/v1/context/initialize -- request, response and error cases."""

from __future__ import annotations

from uc02.infrastructure.providers.mocks import (
    CoursesScenario,
    HistoryScenario,
    LegalScenario,
    NaricScenario,
)
from tests.fixtures.factories import auth, make_client, make_harness, make_settings


def test_initialize_returns_201_and_the_documented_shape():
    harness = make_harness(naric=NaricScenario.LEVEL_7)
    client = make_client(harness)

    response = client.post(
        "/api/v1/context/initialize",
        json={"session_id": "sess-1"},
        headers=auth("learner-1"),
    )
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "session_id",
        "context_status",
        "context_version",
        "built_at",
        "explanation_profile",
        "courses",
        "legal_profile",
        "question_history",
        "personalization",
        "source_status",
    }
    assert body["session_id"] == "sess-1"
    assert body["context_status"] == "created"
    assert body["context_version"] == "uc02.context.v1"
    assert body["explanation_profile"]["template_id"] == "advanced"


def test_response_carries_course_and_legal_context():
    harness = make_harness(courses=CoursesScenario.SINGLE_ENROLMENT)
    client = make_client(harness)
    body = client.post(
        "/api/v1/context/initialize", json={"session_id": "s"}, headers=auth("learner-1")
    ).json()

    enrolment = body["courses"]["enrolments"][0]
    assert enrolment["course_name"] == "Contract Law Foundations"
    assert enrolment["completion_percentage"] == 42.5
    assert enrolment["last_accessed_lesson_name"] == "Offer and Acceptance"
    assert body["legal_profile"]["practice_area"] == "Commercial litigation"
    assert body["legal_profile"]["explanation_domain"] == "speciality"


def test_response_carries_history_metadata_only():
    harness = make_harness(history=HistoryScenario.MORE_THAN_20_AVAILABLE)
    client = make_client(harness)
    body = client.post(
        "/api/v1/context/initialize", json={"session_id": "s"}, headers=auth("learner-1")
    ).json()

    history = body["question_history"]
    assert set(history) == {"count", "earliest_asked_at", "latest_asked_at", "truncated"}
    assert history["count"] == 20
    assert history["truncated"] is True


def test_response_carries_source_status_for_all_four_sources():
    harness = make_harness(
        naric=NaricScenario.UNAVAILABLE,
        courses=CoursesScenario.EMPTY,
        legal=LegalScenario.MISSING_SPECIALITY,
        history=HistoryScenario.ZERO,
    )
    client = make_client(harness)
    body = client.post(
        "/api/v1/context/initialize", json={"session_id": "s"}, headers=auth("learner-1")
    ).json()

    statuses = {name: entry["status"] for name, entry in body["source_status"].items()}
    assert statuses == {
        "naric": "unavailable",
        "courses": "empty",
        "legal_profile": "partial",
        "question_history": "empty",
    }
    assert body["source_status"]["naric"]["fallback_applied"] is True
    assert body["source_status"]["courses"]["fallback_applied"] is False


def test_all_sources_down_returns_200_class_response_with_the_notice():
    harness = make_harness(
        naric=NaricScenario.UNAVAILABLE,
        courses=CoursesScenario.UNAVAILABLE,
        legal=LegalScenario.UNAVAILABLE,
        history=HistoryScenario.UNAVAILABLE,
    )
    client = make_client(harness)
    response = client.post(
        "/api/v1/context/initialize", json={"session_id": "s"}, headers=auth("learner-1")
    )
    assert response.status_code == 201
    body = response.json()
    assert body["personalization"] == {
        "available": False,
        "notice": (
            "Personalisation data is temporarily unavailable. "
            "You can continue your session."
        ),
        "contributing_sources": [],
    }
    assert body["explanation_profile"]["template_id"] == "intermediate"


def test_missing_identity_header_is_unauthenticated():
    client = make_client(make_harness())
    response = client.post("/api/v1/context/initialize", json={"session_id": "s"})
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"


def test_missing_session_id_is_rejected_when_dev_ids_are_disabled():
    client = make_client(make_harness())
    response = client.post("/api/v1/context/initialize", json={}, headers=auth("learner-1"))
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "session_id_required"
    assert "UC-01" in body["detail"]


def test_dev_session_ids_are_minted_only_when_explicitly_enabled():
    settings = make_settings(allow_dev_session_ids=True)
    client = make_client(make_harness(settings=settings))
    response = client.post("/api/v1/context/initialize", json={}, headers=auth("learner-1"))
    assert response.status_code == 201
    assert response.json()["session_id"].startswith("dev-session-")


def test_a_caller_supplied_session_id_is_treated_as_opaque():
    client = make_client(make_harness())
    weird = "urn:uc01:session:9f3b/ABC-123"
    body = client.post(
        "/api/v1/context/initialize", json={"session_id": weird}, headers=auth("learner-1")
    ).json()
    assert body["session_id"] == weird


def test_status_endpoint_returns_flags_without_content():
    harness = make_harness()
    client = make_client(harness)
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-1")
    )

    response = client.get("/api/v1/context/sess-1/status", headers=auth("learner-1"))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "session_id",
        "exists",
        "context_version",
        "built_at",
        "personalization_available",
        "source_status",
    }
    assert body["exists"] is True
    assert "explanation_profile" not in body
    assert "courses" not in body
    assert "legal_profile" not in body


def test_status_endpoint_404s_for_an_unknown_session():
    client = make_client(make_harness())
    response = client.get("/api/v1/context/never-built/status", headers=auth("learner-1"))
    assert response.status_code == 404
    assert response.json()["error"] == "context_not_found"


def test_health_endpoint_reports_configuration():
    client = make_client(make_harness())
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["providers"] == {
        "naric": "mock",
        "courses": "mock",
        "legal": "mock",
        "history": "mock",
    }
    assert body["guards"]["allow_dev_session_ids"] is False


def test_a_caller_cannot_request_a_different_history_limit():
    """The 20-question limit is the server's. A caller asking for 500 gets 20."""
    harness = make_harness(history=HistoryScenario.MORE_THAN_20_AVAILABLE)
    client = make_client(harness)

    rejected = client.post(
        "/api/v1/context/initialize",
        json={"session_id": "sess-1", "question_history_limit": 500, "limit": 500},
        headers=auth("learner-1"),
    )
    assert rejected.status_code == 422
    assert harness.provider_call_count == 0

    body = client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-1")
    ).json()
    assert harness.history.observed_limits == [20]
    assert body["question_history"]["count"] == 20
    assert body["question_history"]["truncated"] is True
