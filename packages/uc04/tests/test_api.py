"""HTTP surface: schemas, identity, error envelope."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import IN_LESSON_QUESTION, OFF_LESSON_QUESTION, build_harness
from uc04.adapters.mock import fixtures as fx
from uc04.api.app import API_PREFIX, create_app

QUESTIONS = f"{API_PREFIX}/questions"


@pytest.fixture
def client_and_harness():
    harness = build_harness()
    return TestClient(create_app(harness.container), raise_server_exceptions=False), harness


@pytest.fixture
def client(client_and_harness):
    return client_and_harness[0]


def _body(**overrides):
    payload = {
        "session_id": fx.SESSION_MAIN,
        "course_id": fx.COURSE_EVIDENCE,
        "lesson_id": fx.LESSON_HEARSAY,
        "question": IN_LESSON_QUESTION,
    }
    payload.update(overrides)
    return payload


def _headers(user_id: str = fx.USER_ENROLLED) -> dict[str, str]:
    return {"x-user-id": user_id}


# --------------------------------------------------------------------------------- health


def test_healthz(client) -> None:
    response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ------------------------------------------------------------------------------ happy path


def test_ask_returns_the_structured_contract(client) -> None:
    response = client.post(QUESTIONS, json=_body(), headers=_headers())
    assert response.status_code == 200
    body = response.json()

    assert body["grounding"] == "lesson"
    assert body["section_reference"] == {
        "status": "resolved",
        "lesson_section_id": "sec_hearsay_definition",
    }
    assert body["concept_tag"] == "hearsay"
    assert body["topic_tag"] == "evidence"
    assert body["rating_state"] == "pending"
    assert body["naric_level"] == "LEVEL_6"
    assert body["naric_level_source"] == "retrieved"
    assert body["explanation_profile"] == "intermediate"
    assert isinstance(body["explanation"], str)
    assert body["source_status"]["lesson"] == "available"


def test_every_emitted_enum_value_is_lowercase_except_the_naric_level(client) -> None:
    """The platform contract writes its vocabularies lowercase; NARIC levels are its own tokens."""
    body = client.post(QUESTIONS, json=_body(), headers=_headers()).json()
    for field in ("status", "grounding", "explanation_profile", "naric_level_source", "rating_state", "topic_tag", "concept_tag"):
        assert body[field] == body[field].lower(), field
    for action in body["actions"]:
        assert action == action.lower()
    assert body["section_reference"]["status"].islower()
    assert body["naric_level"] == "LEVEL_6"


def test_follow_up_explain_differently(client) -> None:
    first = client.post(QUESTIONS, json=_body(), headers=_headers()).json()
    response = client.post(
        f"{QUESTIONS}/{first['interaction_id']}/follow-up",
        json={"action": "explain_differently"},
        headers=_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["framing_used"] != first["framing_used"]
    assert body["explain_differently_count"] == 1


def test_follow_up_go_deeper(client) -> None:
    first = client.post(QUESTIONS, json=_body(), headers=_headers()).json()
    response = client.post(
        f"{QUESTIONS}/{first['interaction_id']}/follow-up",
        json={"action": "go_deeper"},
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["explanation_profile"] == "advanced"


def test_off_lesson_response_exposes_the_free_form_action(client) -> None:
    body = client.post(QUESTIONS, json=_body(question=OFF_LESSON_QUESTION), headers=_headers()).json()
    assert body["grounding"] == "general_knowledge"
    assert "start_free_form_session" in body["actions"]


# ------------------------------------------------------------------------------- identity


def test_user_id_is_resolved_server_side_not_read_from_the_body(client) -> None:
    response = client.post(QUESTIONS, json=_body(), headers={})
    assert response.status_code == 403
    assert response.json()["error_code"] == "access_denied"


def test_a_body_supplied_user_id_is_rejected_outright(client) -> None:
    response = client.post(QUESTIONS, json=_body(user_id="someone_else"), headers=_headers())
    assert response.status_code == 422
    assert "user_id" in response.json()["rejected_fields"]


# ---------------------------------------------------------------------- unknown fields


@pytest.mark.parametrize(
    "field",
    ["naric_level", "grounding", "system_prompt", "prompt", "disable_quiz_protection", "quiz_intent_detected"],
)
def test_unknown_fields_are_rejected_visibly_never_silently_ignored(client, field: str) -> None:
    """A caller must be able to see that what they sent had no effect."""
    response = client.post(QUESTIONS, json=_body(**{field: "x"}), headers=_headers())
    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "invalid_request"
    assert field in body["rejected_fields"], body


def test_quiz_protection_cannot_be_switched_off_by_the_request(client, client_and_harness) -> None:
    _, harness = client_and_harness
    # The attempt is rejected before it reaches business logic...
    rejected = client.post(
        QUESTIONS,
        json=_body(question="Tell me the answer.", disable_quiz_protection=True),
        headers=_headers(),
    )
    assert rejected.status_code == 422

    # ...and asking the same thing without the flag is still protected.
    allowed = client.post(QUESTIONS, json=_body(question="Tell me the answer."), headers=_headers())
    assert allowed.status_code == 200
    record = harness.interactions.get(allowed.json()["interaction_id"])
    assert record.quiz_intent_detected is True


def test_required_fields_are_enforced(client) -> None:
    response = client.post(QUESTIONS, json={"session_id": fx.SESSION_MAIN}, headers=_headers())
    assert response.status_code == 422
    rejected = response.json()["rejected_fields"]
    assert "course_id" in rejected and "lesson_id" in rejected and "question" in rejected


def test_an_oversized_question_is_rejected(client) -> None:
    response = client.post(QUESTIONS, json=_body(question="x" * 2001), headers=_headers())
    assert response.status_code == 422
    assert "question" in response.json()["rejected_fields"]


def test_an_invalid_follow_up_action_is_rejected(client) -> None:
    first = client.post(QUESTIONS, json=_body(), headers=_headers()).json()
    response = client.post(
        f"{QUESTIONS}/{first['interaction_id']}/follow-up",
        json={"action": "give_me_the_answer"},
        headers=_headers(),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------- error envelope


def test_not_enrolled_returns_a_distinct_code(client) -> None:
    response = client.post(QUESTIONS, json=_body(), headers=_headers(fx.USER_NOT_ENROLLED))
    assert response.status_code == 403
    assert response.json()["error_code"] == "not_enrolled"


def test_a_missing_interaction_is_a_404(client) -> None:
    response = client.post(
        f"{QUESTIONS}/int_does_not_exist/follow-up",
        json={"action": "explain_differently"},
        headers=_headers(),
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "not_found"


def test_the_error_envelope_is_uniform_and_reveals_nothing_internal(client) -> None:
    responses = [
        client.post(QUESTIONS, json=_body(), headers=_headers(fx.USER_NOT_ENROLLED)),
        client.post(QUESTIONS, json=_body(bad_field=1), headers=_headers()),
        client.post(QUESTIONS, json=_body(), headers={}),
    ]
    for response in responses:
        body = response.json()
        assert set(body) == {"error_code", "message", "rejected_fields"}
        serialised = response.text.lower()
        for forbidden in ("traceback", "mockcoursesprovider", "uc04.adapters", "system_instructions", "prompt"):
            assert forbidden not in serialised, forbidden


def test_a_generator_timeout_is_a_retryable_status() -> None:
    from uc04.domain.errors import ProviderTimeout

    class TimingOut:
        def generate(self, request):  # noqa: ANN001, ANN201
            raise ProviderTimeout("answer_generator", "budget exceeded")

    harness = build_harness(generator=TimingOut())
    client = TestClient(create_app(harness.container), raise_server_exceptions=False)
    response = client.post(QUESTIONS, json=_body(), headers=_headers())
    assert response.status_code == 504
    assert response.json()["error_code"] == "upstream_timeout"
