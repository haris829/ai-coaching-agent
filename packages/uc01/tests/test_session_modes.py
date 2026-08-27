"""Session mode selection: the three modes open, and the mode type is explicit."""

from __future__ import annotations

import pytest

from uc01.domain.enums import SessionMode, SessionStatus

from .conftest import CAROL, auth, scenarios


def test_free_form_session_opens(client):
    response = client.post("/api/v1/sessions", headers=auth(), json={"mode": "free-form"})
    assert response.status_code == 201
    body = response.json()
    assert body["session"]["session_type"] == "free-form"
    assert body["session"]["status"] == "active"
    assert body["session"]["linked_resource"] is None
    assert body["greeting"]["text"]


def test_course_linked_session_opens(client):
    response = client.post(
        "/api/v1/sessions",
        headers=auth(),
        json={
            "mode": "course-linked",
            "course_id": "crs_contract_law",
            "lesson_id": "lsn_offer",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["session"]["session_type"] == "course-linked"
    linked = body["session"]["linked_resource"]
    assert linked["type"] == "course"
    assert linked["id"] == "crs_contract_law"
    assert linked["secondary_id"] == "lsn_offer"
    # The greeting must reference the selected course and lesson.
    assert "Contract Law Foundations" in body["greeting"]["text"]
    assert "Offer and Acceptance" in body["greeting"]["text"]


def test_case_linked_session_opens(client):
    response = client.post(
        "/api/v1/sessions", headers=auth(), json={"mode": "case-linked", "case_id": "case_alpha"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["session"]["session_type"] == "case-linked"
    assert body["session"]["linked_resource"]["type"] == "case_file"
    assert "Alpha Holdings v. Brookfield" in body["greeting"]["text"]


def test_bootstrap_lists_exactly_the_three_modes(client):
    body = client.get("/api/v1/session-bootstrap", headers=auth()).json()
    assert [mode["mode"] for mode in body["modes"]] == [
        "free-form",
        "course-linked",
        "case-linked",
    ]
    assert all(mode["available"] for mode in body["modes"])


def test_free_form_is_available_even_when_every_dependency_fails(client):
    body = client.get(
        "/api/v1/session-bootstrap",
        headers={
            **auth(),
            **scenarios(
                courses="unavailable",
                cases="unavailable",
                naric="unavailable",
                profile="unavailable",
            ),
        },
    ).json()
    availability = {mode["mode"]: mode["available"] for mode in body["modes"]}
    assert availability == {
        "free-form": True,
        "course-linked": False,
        "case-linked": False,
    }

    opened = client.post(
        "/api/v1/sessions",
        headers={
            **auth(),
            **scenarios(
                courses="unavailable",
                cases="unavailable",
                naric="unavailable",
                profile="unavailable",
            ),
        },
        json={"mode": "free-form"},
    )
    assert opened.status_code == 201
    assert opened.json()["session"]["status"] == "degraded"


@pytest.mark.parametrize("bad_mode", ["freeform", "FREE-FORM", "course", "", "admin"])
def test_unknown_mode_is_rejected(client, bad_mode):
    response = client.post("/api/v1/sessions", headers=auth(), json={"mode": bad_mode})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_session_mode_enum_parse_is_strict():
    assert SessionMode.parse("free-form") is SessionMode.FREE_FORM
    with pytest.raises(ValueError):
        SessionMode.parse("free_form")
    with pytest.raises(ValueError):
        SessionMode.parse(None)


def test_mode_specific_selections_are_validated_server_side(client):
    # Free-form must not carry a linked resource.
    response = client.post(
        "/api/v1/sessions",
        headers=auth(),
        json={"mode": "free-form", "course_id": "crs_contract_law"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "selection_not_allowed"

    # Course-linked must not also carry a case.
    response = client.post(
        "/api/v1/sessions",
        headers=auth(),
        json={
            "mode": "course-linked",
            "course_id": "crs_contract_law",
            "lesson_id": "lsn_offer",
            "case_id": "case_alpha",
        },
    )
    assert response.status_code == 400

    # Case-linked must not also carry a course.
    response = client.post(
        "/api/v1/sessions",
        headers=auth(),
        json={"mode": "case-linked", "case_id": "case_alpha", "course_id": "crs_contract_law"},
    )
    assert response.status_code == 400


def test_case_linked_and_free_form_survive_a_courses_outage(client):
    headers = {**auth(), **scenarios(courses="unavailable")}
    assert client.post("/api/v1/sessions", headers=headers, json={"mode": "free-form"}).status_code == 201
    assert (
        client.post(
            "/api/v1/sessions", headers=headers, json={"mode": "case-linked", "case_id": "case_alpha"}
        ).status_code
        == 201
    )


def test_course_linked_survives_a_case_service_outage(client):
    headers = {**auth(), **scenarios(cases="unavailable")}
    response = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"mode": "course-linked", "course_id": "crs_contract_law", "lesson_id": "lsn_offer"},
    )
    assert response.status_code == 201
    # The case outage is not even consulted for a course-linked open.
    assert response.json()["session"]["status"] == "active"


def test_user_without_courses_sees_course_mode_disabled_but_case_mode_working(client):
    body = client.get("/api/v1/session-bootstrap", headers=auth(CAROL)).json()
    availability = {mode["mode"]: mode for mode in body["modes"]}
    assert availability["course-linked"]["available"] is False
    assert availability["course-linked"]["reason"]
    assert availability["case-linked"]["available"] is True

    opened = client.post(
        "/api/v1/sessions", headers=auth(CAROL), json={"mode": "case-linked", "case_id": "case_beta"}
    )
    assert opened.status_code == 201


def test_degraded_status_is_used_not_failed_when_session_opens(client):
    response = client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(profile="unavailable")},
        json={"mode": "free-form"},
    )
    assert response.status_code == 201
    assert response.json()["session"]["status"] == SessionStatus.DEGRADED.value
    assert response.json()["session"]["failure_code"] is None
