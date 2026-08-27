"""Structured logging: technical detail is logged server-side, not returned."""

from __future__ import annotations

import json
import logging

from uc01.logging_setup import JsonLogFormatter

from .conftest import auth, scenarios


def test_json_formatter_emits_a_stable_envelope():
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="uc01.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="dependency.courses.failed",
        args=(),
        exc_info=None,
    )
    record.uc01 = {"dependency": "courses", "detail": "HTTP 503 from upstream"}
    payload = json.loads(formatter.format(record))

    assert payload["event"] == "dependency.courses.failed"
    assert payload["level"] == "WARNING"
    assert payload["use_case"] == "UC-01"
    assert payload["context"]["dependency"] == "courses"
    assert payload["timestamp"].endswith("+00:00")


def test_json_formatter_includes_the_traceback_server_side():
    formatter = JsonLogFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord(
            name="uc01.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="api.unhandled_exception",
            args=(),
            exc_info=True,
        )
        import sys

        record.exc_info = sys.exc_info()
    payload = json.loads(formatter.format(record))
    assert "RuntimeError: boom" in payload["exception"]


def test_dependency_failure_is_logged_with_detail_but_not_returned(client, caplog):
    with caplog.at_level(logging.WARNING, logger="uc01"):
        response = client.post(
            "/api/v1/sessions",
            headers={**auth(), **scenarios(courses="unavailable")},
            json={"mode": "course-linked", "course_id": "crs_contract_law", "lesson_id": "lsn_offer"},
        )

    assert response.status_code == 409
    # The technical detail from the adapter is in the logs...
    logged = " ".join(
        json.dumps(getattr(record, "uc01", {})) + record.getMessage()
        for record in caplog.records
    )
    assert "dependency.courses.failed" in logged
    assert "simulated Courses Agent timeout" in logged
    # ...and nowhere in the response.
    assert "simulated" not in response.text
    assert "timeout" not in response.text


def test_session_lifecycle_is_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger="uc01"):
        client.post("/api/v1/sessions", headers=auth(), json={"mode": "free-form"})
    events = [record.getMessage() for record in caplog.records]
    assert "session.initializing" in events
    assert "session.opened" in events


def test_cross_user_access_attempt_is_logged(client, caplog):
    created = client.post("/api/v1/sessions", headers=auth("dev-alice"), json={"mode": "free-form"})
    session_id = created.json()["session"]["session_id"]
    with caplog.at_level(logging.INFO, logger="uc01"):
        client.get(f"/api/v1/sessions/{session_id}", headers=auth("dev-bob"))
    assert "session.access_denied" in [record.getMessage() for record in caplog.records]
