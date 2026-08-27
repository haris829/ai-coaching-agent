"""API surface: schemas, documented endpoints, and the UI states the API must support."""

from __future__ import annotations

from .conftest import ALICE, BOB, auth, scenarios

EXPECTED_PATHS = {
    "/api/v1/session-bootstrap",
    "/api/v1/courses",
    "/api/v1/case-files",
    "/api/v1/sessions",
    "/api/v1/sessions/{session_id}",
    "/api/v1/healthz",
    "/api/v1/dev/context",
}


def test_the_api_exposes_exactly_the_uc01_endpoints(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == EXPECTED_PATHS


def test_openapi_documents_request_and_response_schemas(client):
    spec = client.get("/openapi.json").json()
    post = spec["paths"]["/api/v1/sessions"]["post"]
    assert post["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "OpenSessionRequest"
    )
    assert "201" in post["responses"]
    assert {"400", "401", "403", "409", "422", "503"} <= set(post["responses"])

    schema = spec["components"]["schemas"]["OpenSessionRequest"]
    assert schema["additionalProperties"] is False, "requests must forbid unknown fields"
    assert set(schema["properties"]) == {
        "mode",
        "course_id",
        "lesson_id",
        "case_id",
        "continue_without_calibration",
        "on_dependency_failure",
    }


def test_health_endpoint_declares_that_mocks_are_in_use(client):
    body = client.get("/api/v1/healthz").json()
    assert body["status"] == "ok"
    assert body["use_case"].startswith("UC-01")
    assert body["integrations"]["using_mock_adapters"] is True
    assert "fixtures, not real integrations" in body["integrations"]["warning"]


def test_bootstrap_response_shape(client):
    body = client.get("/api/v1/session-bootstrap", headers=auth()).json()
    assert set(body) == {
        "user_id",
        "display_name",
        "personalisation_available",
        "modes",
        "courses",
        "case_files",
        "naric",
        "dependencies",
        "notices",
        "greeting_preview",
        "integrations",
    }
    assert set(body["naric"]) == {
        "level",
        "source",
        "is_fallback",
        "offer_continue_without_calibration",
        "notice",
    }
    assert set(body["modes"][0]) == {"mode", "available", "reason"}


def test_session_response_shape(client):
    body = client.post("/api/v1/sessions", headers=auth(), json={"mode": "free-form"}).json()
    assert set(body) == {"session", "greeting", "context", "notices"}
    assert set(body["session"]) == {
        "session_id",
        "user_id",
        "session_type",
        "status",
        "requested_mode",
        "downgraded_from",
        "linked_resource",
        "naric_level",
        "naric_level_source",
        "explanation_level",
        "degraded_dependencies",
        "failure_code",
        "created_at",
        "updated_at",
    }
    # Server-only material is absent.
    assert "diagnostics" not in body["session"]
    assert "system_prompt_id" not in body["session"]


def test_get_session_returns_the_created_record(client):
    created = client.post("/api/v1/sessions", headers=auth(), json={"mode": "free-form"}).json()
    fetched = client.get(
        f"/api/v1/sessions/{created['session']['session_id']}", headers=auth()
    ).json()
    assert fetched == created["session"]


def test_dev_context_powers_the_reference_ui(client):
    body = client.get("/api/v1/dev/context", headers=auth()).json()
    assert {user["user_id"] for user in body["users"]} == {"u_alice", "u_bob", "u_carol"}
    assert set(body["scenario_options"]) == {"naric", "courses", "cases", "profile"}
    assert "unavailable" in body["scenario_options"]["courses"]
    assert body["scenario_header_enabled"] is True


# --------------------------------------------------------------------------- #
# The four UI states named in the brief, verified through the API
# --------------------------------------------------------------------------- #


def modes_of(client, headers) -> dict[str, dict]:
    body = client.get("/api/v1/session-bootstrap", headers=headers).json()
    return {mode["mode"]: mode for mode in body["modes"]}


def test_ui_state_normal(client):
    modes = modes_of(client, auth(ALICE))
    assert [m["available"] for m in modes.values()] == [True, True, True]
    assert all(m["reason"] is None for m in modes.values())


def test_ui_state_no_case_files(client):
    modes = modes_of(client, auth(BOB))
    assert modes["free-form"]["available"] is True
    assert modes["course-linked"]["available"] is True
    assert modes["case-linked"]["available"] is False
    assert modes["case-linked"]["reason"] == "No accessible case files."


def test_ui_state_courses_unavailable(client):
    modes = modes_of(client, {**auth(), **scenarios(courses="unavailable")})
    assert modes["free-form"]["available"] is True
    assert modes["course-linked"]["available"] is False
    assert modes["course-linked"]["reason"] == "Courses are temporarily unavailable."
    assert modes["case-linked"]["available"] is True


def test_ui_state_naric_unavailable_does_not_disable_the_session(client):
    headers = {**auth(), **scenarios(naric="unavailable")}
    body = client.get("/api/v1/session-bootstrap", headers=headers).json()
    assert all(mode["available"] for mode in body["modes"])
    assert body["naric"]["offer_continue_without_calibration"] is True
    assert body["naric"]["level"] == 5
    assert "continue without calibration" in body["naric"]["notice"].lower()

    # The affordance is described in a machine-readable way for the UI.
    notice = next(n for n in body["notices"] if n["action"] == "continue_without_calibration")
    assert notice["code"] == "naric_calibration_unavailable"


def test_bootstrap_reflects_the_continue_without_calibration_choice(client):
    headers = {**auth(), **scenarios(naric="unavailable")}
    before = client.get("/api/v1/session-bootstrap", headers=headers).json()
    after = client.get(
        "/api/v1/session-bootstrap?continue_without_calibration=true", headers=headers
    ).json()
    assert before["naric"]["offer_continue_without_calibration"] is True
    assert after["naric"]["offer_continue_without_calibration"] is False
    assert after["naric"]["source"] == "default_user_acknowledged"


def test_frontend_is_served_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from uc01.api.app import create_app
    from uc01.config import Settings

    settings = Settings(
        persistence="memory", serve_frontend=True, database_path=str(tmp_path / "x.sqlite3")
    )
    with TestClient(create_app(settings, configure_logs=False)) as ui_client:
        page = ui_client.get("/")
        assert page.status_code == 200
        assert "Session mode" in page.text
        assert ui_client.get("/static/app.js").status_code == 200
        assert ui_client.get("/static/styles.css").status_code == 200
