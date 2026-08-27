"""Privacy.

This component stores question text, response text and learner comments because the
improvement pipeline requires them.  None of it may reach a log line, a flag, or an error
response.

Every test in the suite is already guarded by the autouse ``no_learner_content_in_logs``
fixture in ``tests/conftest.py``.  The tests here prove that guard is real, and cover the
paths where a leak would be most likely.
"""

from __future__ import annotations

import json
import logging
import pathlib

from tests.canaries import CANARY_FRAGMENTS, canary_comment, contains_canary
from tests.conftest import ADMIN_HEADERS, LEARNER_HEADERS
from tests.helpers import seed_via_api
from uc10.domain.models import LEARNER_CONTENT_FIELDS
from uc10.logging_setup import DENIED_LOG_KEYS, REDACTED, get_logger

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _captured(caplog) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


# ------------------------------------------------- the guard is not vacuous


def test_the_canary_detector_actually_detects_a_leak():
    """If the detector could not see a leak, every privacy assertion would be theatre."""
    leaked = f'{{"event": "oops", "comment": "{canary_comment()}"}}'
    assert contains_canary(leaked)
    assert contains_canary('{"event": "fine", "rating_id": "rat_1"}') == []


def test_every_test_in_the_suite_is_guarded():
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "@pytest.fixture(autouse=True)" in conftest
    assert "def no_learner_content_in_logs" in conftest
    assert "contains_canary" in conftest


def test_the_mock_and_foreign_fixtures_carry_canary_content():
    """The suite only proves something if the data flowing through it is marked."""
    mock = (ROOT / "uc10" / "adapters" / "mock" / "interaction_provider.py").read_text("utf-8")
    foreign = (ROOT / "uc10" / "adapters" / "foreign" / "interaction_provider.py").read_text(
        "utf-8"
    )
    assert "MOCK_QUESTION_TEXT_DO_NOT_LOG" in mock
    assert "MOCK_RESPONSE_TEXT_DO_NOT_LOG" in mock
    assert "FOREIGN_QUESTION_TEXT_DO_NOT_LOG" in foreign
    assert "FOREIGN_RESPONSE_TEXT_DO_NOT_LOG" in foreign


# ------------------------------------------------------------ the full flow


def test_a_complete_rating_and_flagging_flow_logs_identifiers_but_no_content(
    client, interactions, container, caplog
):
    caplog.set_level(logging.DEBUG)

    seed_via_api(
        client, interactions, total=10, downs=10, topic_tag="lease_covenants", with_comments=True
    )
    client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down", "comment": canary_comment("with detail")},
        headers=LEARNER_HEADERS,
    )
    container.flagging.run_cycle()
    flag_id = container.flag_repository.list_open()[0].flag_id
    client.get("/api/v1/admin/flags", headers=ADMIN_HEADERS)
    client.patch(
        f"/api/v1/admin/flags/{flag_id}", json={"status": "reviewed"}, headers=ADMIN_HEADERS
    )

    captured = _captured(caplog)
    assert contains_canary(captured) == []

    # ...and the log is genuinely rich, so the absence above is not an absence of logging.
    assert "rating_recorded" in captured
    assert "flag_created" in captured
    assert "flag_status_changed" in captured
    assert "lease_covenants" in captured
    assert "int_answer" in captured
    for line in captured.splitlines():
        if '"event": "rating_recorded"' in line:
            payload = json.loads(line)
            assert set(payload) >= {"rating_id", "interaction_id", "topic_tag", "rating"}
            assert "comment" not in payload or payload["comment"] == REDACTED
            assert payload["comment_supplied"] is True  # the fact, never the text


def test_a_rejected_rating_logs_no_content(client, caplog):
    caplog.set_level(logging.DEBUG)
    client.post(
        "/api/v1/interactions/int_delivered_25h/rating",
        json={"rating": "down", "comment": canary_comment("too late")},
        headers=LEARNER_HEADERS,
    )
    client.post(
        "/api/v1/interactions/int_unavailable/rating",
        json={"rating": "down", "comment": canary_comment("unavailable")},
        headers=LEARNER_HEADERS,
    )
    client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down", "comment": canary_comment("anonymous")},
    )
    assert contains_canary(_captured(caplog)) == []


# ----------------------------------------------------- the redaction defences


def test_a_denied_key_is_redacted_even_if_a_call_site_passes_it(caplog):
    caplog.set_level(logging.DEBUG)
    get_logger("uc10.test").info(
        "deliberate_mistake",
        rating_id="rat_1",
        question_text="CANARY_QUESTION_SHOULD_NOT_APPEAR",
        response_text="CANARY_RESPONSE_SHOULD_NOT_APPEAR",
        comment="CANARY_COMMENT_SHOULD_NOT_APPEAR",
    )
    captured = _captured(caplog)
    assert "SHOULD_NOT_APPEAR" not in captured
    assert captured.count(REDACTED) == 3
    assert "rat_1" in captured


def test_a_whole_record_is_never_serialised_into_a_log_line(caplog, container, client):
    caplog.set_level(logging.DEBUG)
    client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down", "comment": canary_comment()},
        headers=LEARNER_HEADERS,
    )
    record = container.ratings_repository.all_records()[0]

    get_logger("uc10.test").info("deliberate_mistake", rating=record)

    captured = _captured(caplog)
    assert contains_canary(captured) == []
    assert "<RatingRecord>" in captured


def test_the_denied_key_list_covers_every_learner_content_field():
    assert LEARNER_CONTENT_FIELDS <= DENIED_LOG_KEYS


# --------------------------------------------------- responses and artefacts


def test_a_flag_artefact_carries_no_learner_content(client, interactions, container):
    seed_via_api(
        client, interactions, total=10, downs=10, topic_tag="probate_delays", with_comments=True
    )
    flag = container.flag_repository.list_open()[0]
    serialised = json.dumps(flag.model_dump(mode="json"))
    assert contains_canary(serialised) == []
    assert set(flag.model_dump()) & LEARNER_CONTENT_FIELDS == set()

    api_body = client.get("/api/v1/admin/flags", headers=ADMIN_HEADERS).text
    assert contains_canary(api_body) == []


def test_a_notification_to_the_platform_team_carries_no_learner_content(
    client, interactions, notifications
):
    seed_via_api(
        client, interactions, total=10, downs=10, topic_tag="costs_budgeting", with_comments=True
    )
    assert notifications.notified
    for flag in notifications.notified:
        assert contains_canary(json.dumps(flag.model_dump(mode="json"))) == []


def test_a_validation_error_never_echoes_the_learners_text(client):
    """Pydantic reports the offending input by default; the handler strips it."""
    over_long = canary_comment("x" * 600)
    response = client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down", "comment": over_long},
        headers=LEARNER_HEADERS,
    )
    assert response.status_code == 422
    assert contains_canary(response.text) == []
    assert response.json()["error"]["fields"] == [{"field": "comment", "issue": "string_too_long"}]


def test_no_error_response_in_the_component_carries_learner_content(client):
    cases = [
        ("int_answer", {"rating": "sideways", "comment": canary_comment()}),
        ("int_delivered_25h", {"rating": "down", "comment": canary_comment()}),
        ("int_unavailable", {"rating": "down", "comment": canary_comment()}),
        ("int_other_learner", {"rating": "down", "comment": canary_comment()}),
        ("int_missing", {"rating": "down", "comment": canary_comment()}),
    ]
    for interaction_id, body in cases:
        response = client.post(
            f"/api/v1/interactions/{interaction_id}/rating", json=body, headers=LEARNER_HEADERS
        )
        assert response.status_code >= 400
        assert contains_canary(response.text) == [], (interaction_id, response.text)


def test_the_canary_list_covers_all_three_content_kinds():
    joined = " ".join(CANARY_FRAGMENTS)
    assert "QUESTION" in joined and "RESPONSE" in joined and "COMMENT" in joined
