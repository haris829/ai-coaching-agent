"""Courses Agent: available, unavailable, invalid payload, and rejected selections."""

from __future__ import annotations

from uc01.domain import messages
from uc01.domain.enums import SessionStatus

from .conftest import ALICE, BOB, CAROL, auth, scenarios


def test_courses_available_with_lessons(client):
    body = client.get("/api/v1/courses", headers=auth()).json()
    assert body["available"] is True
    titles = {course["title"] for course in body["courses"]}
    assert "Contract Law Foundations" in titles
    contract = next(c for c in body["courses"] if c["course_id"] == "crs_contract_law")
    assert [lesson["lesson_id"] for lesson in contract["lessons"]] == [
        "lsn_offer",
        "lsn_consideration",
        "lsn_terms",
    ]


def test_courses_empty_for_a_user_with_no_enrolments(client):
    body = client.get("/api/v1/courses", headers=auth(CAROL)).json()
    assert body["available"] is False
    assert body["reason"] == messages.COURSES_EMPTY
    assert body["courses"] == []


def test_courses_unavailable_returns_200_with_a_reason(client):
    body = client.get(
        "/api/v1/courses", headers={**auth(), **scenarios(courses="unavailable")}
    ).json()
    assert body["available"] is False
    assert body["reason"] == messages.COURSES_UNAVAILABLE
    assert body["courses"] == []


def test_courses_invalid_payload_is_treated_as_unavailable(client):
    body = client.get(
        "/api/v1/courses", headers={**auth(), **scenarios(courses="invalid")}
    ).json()
    assert body["available"] is False
    assert body["reason"] == messages.COURSES_UNAVAILABLE


def test_course_linked_mode_disabled_when_courses_unavailable(client):
    body = client.get(
        "/api/v1/session-bootstrap", headers={**auth(), **scenarios(courses="unavailable")}
    ).json()
    modes = {mode["mode"]: mode for mode in body["modes"]}
    assert modes["course-linked"]["available"] is False
    assert modes["course-linked"]["reason"] == messages.COURSES_UNAVAILABLE
    assert modes["free-form"]["available"] is True
    assert modes["case-linked"]["available"] is True
    assert any(notice["code"] == "courses_unavailable" for notice in body["notices"])


def test_course_linked_open_is_rejected_when_courses_unavailable(client):
    response = client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(courses="unavailable")},
        json={"mode": "course-linked", "course_id": "crs_contract_law", "lesson_id": "lsn_offer"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "session_mode_unavailable"
    assert body["error"]["message"] == messages.COURSES_UNAVAILABLE
    # The user is told what they can still do.
    assert "free-form" in body["recovery"]["available_modes"]
    assert body["recovery"]["session_id"]


def test_course_linked_can_fall_back_to_free_form_when_requested(client):
    response = client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(courses="unavailable")},
        json={
            "mode": "course-linked",
            "course_id": "crs_contract_law",
            "lesson_id": "lsn_offer",
            "on_dependency_failure": "fallback_free_form",
        },
    )
    assert response.status_code == 201
    session = response.json()["session"]
    assert session["session_type"] == "free-form"
    assert session["requested_mode"] == "course-linked"
    assert session["downgraded_from"] == "course-linked"
    assert session["status"] == SessionStatus.DEGRADED.value
    assert any(
        notice["code"] == "session_mode_downgraded" for notice in response.json()["notices"]
    )


def test_invalid_course_id_is_rejected(client):
    response = client.post(
        "/api/v1/sessions",
        headers=auth(),
        json={"mode": "course-linked", "course_id": "crs_does_not_exist", "lesson_id": "lsn_offer"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "selection_not_accessible"
    assert response.json()["error"]["message"] == messages.COURSE_NOT_ACCESSIBLE


def test_invalid_lesson_id_is_rejected(client):
    response = client.post(
        "/api/v1/sessions",
        headers=auth(),
        json={"mode": "course-linked", "course_id": "crs_contract_law", "lesson_id": "lsn_nope"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["message"] == messages.LESSON_NOT_ACCESSIBLE


def test_lesson_from_a_different_course_is_rejected(client):
    """A real lesson id, but not one belonging to the selected course."""
    response = client.post(
        "/api/v1/sessions",
        headers=auth(),
        json={"mode": "course-linked", "course_id": "crs_contract_law", "lesson_id": "lsn_hearsay"},
    )
    assert response.status_code == 403


def test_course_without_lessons_cannot_be_opened(client):
    response = client.post(
        "/api/v1/sessions",
        headers=auth(),
        json={"mode": "course-linked", "course_id": "crs_no_lessons", "lesson_id": "lsn_offer"},
    )
    assert response.status_code == 403


def test_inaccessible_course_belonging_to_another_user_is_rejected(client):
    """``crs_tort`` belongs to Bob. Alice must not be able to open it."""
    response = client.post(
        "/api/v1/sessions",
        headers=auth(ALICE),
        json={"mode": "course-linked", "course_id": "crs_tort", "lesson_id": "lsn_duty"},
    )
    assert response.status_code == 403
    # And the message must not reveal whether the course exists.
    assert response.json()["error"]["message"] == messages.COURSE_NOT_ACCESSIBLE

    # Bob can open it.
    allowed = client.post(
        "/api/v1/sessions",
        headers=auth(BOB),
        json={"mode": "course-linked", "course_id": "crs_tort", "lesson_id": "lsn_duty"},
    )
    assert allowed.status_code == 201


def test_missing_course_or_lesson_selection_is_reported_clearly(client):
    no_course = client.post("/api/v1/sessions", headers=auth(), json={"mode": "course-linked"})
    assert no_course.status_code == 400
    assert no_course.json()["error"]["message"] == messages.COURSE_SELECTION_REQUIRED

    no_lesson = client.post(
        "/api/v1/sessions",
        headers=auth(),
        json={"mode": "course-linked", "course_id": "crs_contract_law"},
    )
    assert no_lesson.status_code == 400
    assert no_lesson.json()["error"]["message"] == messages.LESSON_SELECTION_REQUIRED
