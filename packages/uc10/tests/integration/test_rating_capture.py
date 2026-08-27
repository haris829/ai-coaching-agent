"""Rating capture through the HTTP API."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.canaries import canary_comment
from tests.conftest import LEARNER_HEADERS, OTHER_LEARNER_HEADERS
from uc10.adapters.mock.interaction_provider import LEARNER, InteractionSpec
from uc10.domain.enums import RatingValue, ResponseCategory
from uc10.domain.models import REQUIRED_RATING_FIELDS

# Every response category the platform can produce, with the mock scenario for each.
CATEGORY_SCENARIOS = {
    ResponseCategory.ANSWER: "int_answer",
    ResponseCategory.REDIRECT: "int_redirect",
    ResponseCategory.REFUSAL: "int_refusal",
    ResponseCategory.CLARIFYING_QUESTION: "int_clarifying",
    ResponseCategory.DEGRADED_FALLBACK: "int_degraded",
    ResponseCategory.UNKNOWN: "int_unknown_category",
}


def test_no_response_category_is_excluded_from_the_scenario_table():
    """If a category is ever added, this fails until it has a rateability test."""
    assert set(CATEGORY_SCENARIOS) == set(ResponseCategory)


@pytest.mark.parametrize(
    ("category", "interaction_id"), sorted((c, i) for c, i in CATEGORY_SCENARIOS.items())
)
@pytest.mark.parametrize("rating", [RatingValue.UP.value, RatingValue.DOWN.value])
def test_every_response_category_is_rateable(
    client, interactions, category, interaction_id, rating
):
    """No responses are unrateable: answer, redirect, refusal, clarifying question,
    degraded fallback, and a category this component has never seen."""
    assert interactions.get(interaction_id).response_category is category

    response = client.post(
        f"/api/v1/interactions/{interaction_id}/rating",
        json={"rating": rating},
        headers=LEARNER_HEADERS,
    )
    assert response.status_code == 201, response.text
    assert response.json()["rating"]["rating"] == rating


def test_thumbs_down_with_the_comment_dismissed_is_still_logged(client, ratings_repository):
    """Losing the rating because the learner closed the text box would bias the whole
    pipeline towards complaints from people who type."""
    response = client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down"},  # comment box dismissed: field simply absent
        headers=LEARNER_HEADERS,
    )
    assert response.status_code == 201
    stored = ratings_repository.all_records()
    assert len(stored) == 1
    assert stored[0].rating is RatingValue.DOWN
    assert stored[0].comment is None


def test_thumbs_down_with_an_explicitly_null_comment_is_still_logged(client, ratings_repository):
    response = client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down", "comment": None},
        headers=LEARNER_HEADERS,
    )
    assert response.status_code == 201
    assert ratings_repository.count() == 1


def test_a_blank_comment_is_stored_as_no_comment_not_as_empty_text(client, ratings_repository):
    client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down", "comment": "   "},
        headers=LEARNER_HEADERS,
    )
    assert ratings_repository.all_records()[0].comment is None


def test_a_comment_is_stored_when_the_learner_writes_one(client, ratings_repository):
    comment = canary_comment("on formation")
    client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down", "comment": comment},
        headers=LEARNER_HEADERS,
    )
    assert ratings_repository.all_records()[0].comment == comment


def test_every_rating_carries_the_full_required_metadata_set(client, ratings_repository):
    client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down", "comment": canary_comment()},
        headers=LEARNER_HEADERS,
    )
    record = ratings_repository.all_records()[0]
    payload = record.model_dump()

    assert set(payload) == set(REQUIRED_RATING_FIELDS)
    assert all(payload[field] is not None for field in REQUIRED_RATING_FIELDS - {"superseded_by"})
    assert payload["interaction_id"] == "int_answer"
    assert payload["session_id"] == "sess_mock_1"
    assert payload["user_id"] == LEARNER
    assert payload["rating"] is RatingValue.DOWN
    assert payload["naric_level"].value == "level_7"
    assert payload["session_mode"] == "coaching"
    assert payload["topic_tag"] == "contract_formation"
    assert payload["question_text"] and payload["response_text"]
    assert payload["rated_at"].tzinfo is not None


def test_an_anonymous_rating_is_refused_and_never_reaches_the_pipeline(
    client, ratings_repository
):
    response = client.post(
        "/api/v1/interactions/int_answer/rating", json={"rating": "down"}
    )  # no identity header
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "rejected_anonymous"
    assert ratings_repository.count() == 0


def test_a_learner_cannot_rate_on_another_learners_behalf(client, ratings_repository):
    """``user_id`` is resolved server-side; a body field cannot override it, and rating
    someone else's interaction is refused without disclosing that it exists."""
    rejected_body = client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down", "user_id": "user_bob"},
        headers=LEARNER_HEADERS,
    )
    assert rejected_body.status_code == 422
    assert rejected_body.json()["error"]["fields"] == [
        {"field": "user_id", "issue": "extra_forbidden"}
    ]

    other = client.post(
        "/api/v1/interactions/int_other_learner/rating",
        json={"rating": "down"},
        headers=LEARNER_HEADERS,
    )
    assert other.status_code == 404
    assert ratings_repository.count() == 0


@pytest.mark.parametrize(
    "field", ["rated_at", "threshold_applied", "down_rate", "rating_id", "superseded_by"]
)
def test_server_owned_fields_are_rejected_outright(client, field):
    response = client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "up", field: "anything"},
        headers=LEARNER_HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == [{"field": field, "issue": "extra_forbidden"}]


@pytest.mark.parametrize("value", ["thumbs_up", "UP", "1", "", None])
def test_an_unknown_rating_value_is_refused(client, value):
    response = client.post(
        "/api/v1/interactions/int_answer/rating", json={"rating": value}, headers=LEARNER_HEADERS
    )
    assert response.status_code == 422


def test_rating_is_optional_and_an_unrated_response_reads_back_as_none(client):
    response = client.get("/api/v1/interactions/int_answer/rating", headers=LEARNER_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"interaction_id": "int_answer", "rating": None}


def test_a_learner_cannot_read_another_learners_rating(client, interactions):
    """Both learners rate their own interaction; neither read exposes the other's."""
    interactions.register(
        InteractionSpec(
            interaction_id="int_bob_own",
            user_id="user_bob",
            delivered_offset=timedelta(minutes=1),
        )
    )
    client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down", "comment": canary_comment("alice")},
        headers=LEARNER_HEADERS,
    )
    client.post(
        "/api/v1/interactions/int_bob_own/rating",
        json={"rating": "up"},
        headers=OTHER_LEARNER_HEADERS,
    )

    as_bob = client.get("/api/v1/interactions/int_answer/rating", headers=OTHER_LEARNER_HEADERS)
    assert as_bob.status_code == 200
    assert as_bob.json()["rating"] is None

    as_alice = client.get("/api/v1/interactions/int_answer/rating", headers=LEARNER_HEADERS)
    assert as_alice.json()["rating"]["rating"] == "down"


def test_reading_a_rating_requires_authentication(client):
    assert client.get("/api/v1/interactions/int_answer/rating").status_code == 401


def test_a_rating_response_never_carries_question_or_response_text(client):
    response = client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down"},
        headers=LEARNER_HEADERS,
    )
    body = response.text
    assert "MOCK_QUESTION_TEXT_DO_NOT_LOG" not in body
    assert "MOCK_RESPONSE_TEXT_DO_NOT_LOG" not in body


def test_an_unknown_interaction_is_a_404(client, ratings_repository):
    response = client.post(
        "/api/v1/interactions/int_does_not_exist/rating",
        json={"rating": "up"},
        headers=LEARNER_HEADERS,
    )
    assert response.status_code == 404
    assert ratings_repository.count() == 0
