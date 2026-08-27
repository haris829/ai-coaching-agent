"""Profile / personalisation: available, unavailable, incomplete, generic fallback."""

from __future__ import annotations

from uc01.domain import messages

from .conftest import ALICE, CAROL, auth, scenarios


def test_profile_available_gives_a_personalised_greeting(client):
    body = client.post("/api/v1/sessions", headers=auth(ALICE), json={"mode": "free-form"}).json()
    assert body["greeting"]["personalised"] is True
    assert body["greeting"]["text"].startswith("Hi Alice Osei!")
    assert body["context"]["personalisation_available"] is True


def test_profile_unavailable_still_opens_the_session_with_a_generic_greeting(client):
    response = client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(profile="unavailable")},
        json={"mode": "free-form"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["greeting"]["personalised"] is False
    assert body["greeting"]["text"].startswith("Hi! Welcome back to your coaching session.")
    assert messages.PROFILE_UNAVAILABLE_NOTICE in body["greeting"]["text"]
    assert body["context"]["personalisation_available"] is False
    assert "profile" in body["session"]["degraded_dependencies"]


def test_profile_failure_produces_a_clear_non_technical_notice(client):
    body = client.get(
        "/api/v1/session-bootstrap", headers={**auth(), **scenarios(profile="unavailable")}
    ).json()
    notice = next(
        item for item in body["notices"] if item["code"] == "personalisation_unavailable"
    )
    assert notice["message"] == messages.PROFILE_UNAVAILABLE_NOTICE
    assert notice["action"] == "retry"
    # No technical detail anywhere in the payload.
    for banned in ("HTTP 500", "Traceback", "DependencyUnavailableError", "mock:"):
        assert banned not in str(body)


def test_profile_failure_never_invents_a_name_or_course(client):
    body = client.get(
        "/api/v1/session-bootstrap", headers={**auth(), **scenarios(profile="unavailable")}
    ).json()
    assert body["display_name"] is None
    assert body["personalisation_available"] is False
    text = body["greeting_preview"]["text"]
    assert "Alice" not in text
    assert "Contract Law" not in text


def test_incomplete_profile_is_not_an_error_and_does_not_invent_a_name(client):
    """Carol's profile loads but has no name."""
    body = client.post("/api/v1/sessions", headers=auth(CAROL), json={"mode": "free-form"}).json()
    assert body["greeting"]["personalised"] is False
    assert body["greeting"]["text"].startswith("Hi! Welcome back")
    # A loaded-but-incomplete profile does not get the "could not load" apology.
    assert messages.PROFILE_UNAVAILABLE_NOTICE not in body["greeting"]["text"]

    bootstrap = client.get("/api/v1/session-bootstrap", headers=auth(CAROL)).json()
    assert any(
        notice["code"] == "personalisation_incomplete" for notice in bootstrap["notices"]
    )


def test_incomplete_profile_scenario_for_any_user(client):
    body = client.get(
        "/api/v1/session-bootstrap", headers={**auth(ALICE), **scenarios(profile="incomplete")}
    ).json()
    assert body["display_name"] is None
    notice = next(
        item for item in body["notices"] if item["code"] == "personalisation_incomplete"
    )
    assert notice["message"] == messages.PROFILE_INCOMPLETE_NOTICE
    assert notice["severity"] == "info"


def test_profile_failure_is_not_fatal_for_any_mode(client):
    headers = {**auth(), **scenarios(profile="unavailable")}
    assert client.post("/api/v1/sessions", headers=headers, json={"mode": "free-form"}).status_code == 201
    assert (
        client.post(
            "/api/v1/sessions",
            headers=headers,
            json={"mode": "course-linked", "course_id": "crs_contract_law", "lesson_id": "lsn_offer"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/sessions", headers=headers, json={"mode": "case-linked", "case_id": "case_alpha"}
        ).status_code
        == 201
    )


def test_generic_greeting_still_references_the_course_when_profile_fails(client):
    """Personalisation loss must not lose the session's context."""
    body = client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(profile="unavailable")},
        json={"mode": "course-linked", "course_id": "crs_evidence", "lesson_id": "lsn_hearsay"},
    ).json()
    text = body["greeting"]["text"]
    assert text.startswith("Hi! Welcome back")
    assert "Evidence and Proof" in text
    assert "Hearsay" in text
