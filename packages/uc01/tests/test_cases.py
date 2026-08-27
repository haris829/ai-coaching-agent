"""Case files: available, none accessible, service unavailable, inaccessible case."""

from __future__ import annotations

from uc01.domain import messages

from .conftest import ALICE, BOB, CAROL, auth, scenarios


def test_case_files_available(client):
    body = client.get("/api/v1/case-files", headers=auth()).json()
    assert body["available"] is True
    assert [case["case_id"] for case in body["case_files"]] == ["case_alpha"]
    assert body["case_files"][0]["matter_reference"] == "AH-2026-0142"


def test_no_accessible_case_files_disables_case_mode_only(client):
    """Bob has no case files. That must not break anything else."""
    body = client.get("/api/v1/session-bootstrap", headers=auth(BOB)).json()
    modes = {mode["mode"]: mode for mode in body["modes"]}

    assert modes["case-linked"]["available"] is False
    assert modes["case-linked"]["reason"] == messages.CASES_EMPTY
    assert modes["free-form"]["available"] is True
    assert modes["course-linked"]["available"] is True
    assert any(notice["code"] == "cases_empty" for notice in body["notices"])

    # And both remaining modes really do open.
    assert client.post("/api/v1/sessions", headers=auth(BOB), json={"mode": "free-form"}).status_code == 201
    assert (
        client.post(
            "/api/v1/sessions",
            headers=auth(BOB),
            json={"mode": "course-linked", "course_id": "crs_tort", "lesson_id": "lsn_duty"},
        ).status_code
        == 201
    )


def test_case_mode_rejected_when_user_has_no_case_files(client):
    response = client.post(
        "/api/v1/sessions", headers=auth(BOB), json={"mode": "case-linked", "case_id": "case_alpha"}
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["message"] == messages.CASES_EMPTY
    assert set(body["recovery"]["available_modes"]) == {"free-form", "course-linked"}


def test_case_service_unavailable(client):
    headers = {**auth(), **scenarios(cases="unavailable")}
    listing = client.get("/api/v1/case-files", headers=headers).json()
    assert listing["available"] is False
    assert listing["reason"] == messages.CASES_UNAVAILABLE

    bootstrap = client.get("/api/v1/session-bootstrap", headers=headers).json()
    modes = {mode["mode"]: mode for mode in bootstrap["modes"]}
    assert modes["case-linked"]["available"] is False
    assert modes["case-linked"]["reason"] == messages.CASES_UNAVAILABLE
    assert modes["free-form"]["available"] is True
    assert modes["course-linked"]["available"] is True

    rejected = client.post(
        "/api/v1/sessions", headers=headers, json={"mode": "case-linked", "case_id": "case_alpha"}
    )
    assert rejected.status_code == 409


def test_case_service_invalid_payload_is_treated_as_unavailable(client):
    body = client.get(
        "/api/v1/case-files", headers={**auth(), **scenarios(cases="invalid")}
    ).json()
    assert body["available"] is False
    assert body["reason"] == messages.CASES_UNAVAILABLE


def test_inaccessible_case_belonging_to_another_user_is_rejected(client):
    """``case_beta`` belongs to Carol."""
    response = client.post(
        "/api/v1/sessions", headers=auth(ALICE), json={"mode": "case-linked", "case_id": "case_beta"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["message"] == messages.CASE_NOT_ACCESSIBLE

    allowed = client.post(
        "/api/v1/sessions", headers=auth(CAROL), json={"mode": "case-linked", "case_id": "case_beta"}
    )
    assert allowed.status_code == 201


def test_unknown_case_id_is_rejected(client):
    response = client.post(
        "/api/v1/sessions", headers=auth(), json={"mode": "case-linked", "case_id": "case_nope"}
    )
    assert response.status_code == 403


def test_missing_case_selection_is_reported_clearly(client):
    response = client.post("/api/v1/sessions", headers=auth(), json={"mode": "case-linked"})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == messages.CASE_SELECTION_REQUIRED


def test_case_outage_does_not_break_the_rest_of_the_interface(client):
    """The whole bootstrap must still succeed with a case-service outage."""
    response = client.get(
        "/api/v1/session-bootstrap", headers={**auth(), **scenarios(cases="unavailable")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["greeting_preview"]["text"]
    assert body["courses"]
    assert body["naric"]["level"]
