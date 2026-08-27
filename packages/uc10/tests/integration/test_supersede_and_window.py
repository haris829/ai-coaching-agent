"""Changed ratings and the 24-hour historical rating window."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.canaries import canary_comment
from tests.conftest import LEARNER_HEADERS
from uc10.adapters.mock.interaction_provider import InteractionSpec
from uc10.domain.enums import RatingValue

# ------------------------------------------------------------------- supersede


def test_a_changed_rating_supersedes_the_previous_one_which_is_retained_not_deleted(
    client, ratings_repository
):
    first = client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down", "comment": canary_comment("first impression")},
        headers=LEARNER_HEADERS,
    )
    assert first.status_code == 201
    first_id = first.json()["rating"]["rating_id"]

    second = client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "up"},
        headers=LEARNER_HEADERS,
    )
    assert second.status_code == 200
    second_id = second.json()["rating"]["rating_id"]
    assert second.json()["status"] == "replaced"
    assert second.json()["superseded_rating_id"] == first_id

    stored = {record.rating_id: record for record in ratings_repository.all_records()}
    assert len(stored) == 2, "the previous rating must be retained, not deleted"
    assert stored[first_id].superseded_by == second_id
    assert stored[first_id].rating is RatingValue.DOWN
    assert stored[first_id].comment is not None, "the superseded comment is retained too"
    assert stored[second_id].superseded_by is None


def test_the_most_recent_rating_is_authoritative(client):
    client.post(
        "/api/v1/interactions/int_answer/rating", json={"rating": "down"}, headers=LEARNER_HEADERS
    )
    client.post(
        "/api/v1/interactions/int_answer/rating", json={"rating": "up"}, headers=LEARNER_HEADERS
    )
    client.post(
        "/api/v1/interactions/int_answer/rating", json={"rating": "down"}, headers=LEARNER_HEADERS
    )

    current = client.get("/api/v1/interactions/int_answer/rating", headers=LEARNER_HEADERS).json()
    assert current["rating"]["rating"] == "down"
    assert current["rating"]["superseded_by"] is None


def test_a_flip_from_down_to_up_leaves_one_current_rating_for_the_rolling_rate(
    client, container, ratings_repository, clock
):
    """A learner flipping their verdict must not be counted twice in the down rate."""
    client.post(
        "/api/v1/interactions/int_answer/rating", json={"rating": "down"}, headers=LEARNER_HEADERS
    )
    client.post(
        "/api/v1/interactions/int_answer/rating", json={"rating": "up"}, headers=LEARNER_HEADERS
    )

    window = container.flagging.current_window()
    current = ratings_repository.current_in_window(window.start, window.end)
    assert len(current) == 1
    assert current[0].rating is RatingValue.UP
    assert len(ratings_repository.all_records()) == 2  # history retained


def test_the_full_history_of_a_learners_ratings_is_available_to_the_pipeline(
    client, container
):
    client.post(
        "/api/v1/interactions/int_answer/rating", json={"rating": "down"}, headers=LEARNER_HEADERS
    )
    client.post(
        "/api/v1/interactions/int_answer/rating", json={"rating": "up"}, headers=LEARNER_HEADERS
    )
    history = container.ratings.history_for(interaction_id="int_answer", user_id="user_alice")
    assert [record.rating.value for record in history] == ["down", "up"]
    assert [record.superseded_by is None for record in history] == [False, True]


# -------------------------------------------------------------- 24-hour window


def test_a_rating_23_hours_after_delivery_is_accepted(client, ratings_repository):
    response = client.post(
        "/api/v1/interactions/int_delivered_23h/rating",
        json={"rating": "down"},
        headers=LEARNER_HEADERS,
    )
    assert response.status_code == 201
    assert ratings_repository.count() == 1


def test_a_rating_25_hours_after_delivery_is_refused_clearly(client, ratings_repository):
    response = client.post(
        "/api/v1/interactions/int_delivered_25h/rating",
        json={"rating": "down"},
        headers=LEARNER_HEADERS,
    )
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "rejected_window_expired"
    assert "rated for a limited period" in body["message"]
    assert ratings_repository.count() == 0


@pytest.mark.parametrize(
    ("hours", "expected_status"),
    [(0, 201), (1, 201), (23, 201), (23.999, 201), (24, 201), (24.001, 409), (25, 409), (72, 409)],
)
def test_the_window_boundary_is_exactly_the_configured_24_hours(
    make_client, interactions, hours, expected_status
):
    client = make_client()
    interaction_id = f"int_age_{str(hours).replace('.', '_')}"
    interactions.register(
        InteractionSpec(interaction_id=interaction_id, delivered_offset=timedelta(hours=hours))
    )
    response = client.post(
        f"/api/v1/interactions/{interaction_id}/rating",
        json={"rating": "down"},
        headers=LEARNER_HEADERS,
    )
    assert response.status_code == expected_status


def test_a_client_supplied_timestamp_cannot_extend_the_window(client, ratings_repository):
    """The window is computed server-side from the interaction's delivery time. A client
    timestamp is not merely ignored -- the request schema refuses it."""
    for field in ("rated_at", "delivered_at", "now"):
        response = client.post(
            "/api/v1/interactions/int_delivered_25h/rating",
            json={"rating": "down", field: "2026-06-01T11:59:00Z"},
            headers=LEARNER_HEADERS,
        )
        assert response.status_code == 422
        assert response.json()["error"]["fields"] == [
            {"field": field, "issue": "extra_forbidden"}
        ]
    assert ratings_repository.count() == 0


def test_the_window_is_measured_from_delivery_time_not_from_first_seen_time(
    make_client, interactions, clock
):
    """The same interaction becomes unrateable as the clock passes delivery + 24h."""
    client = make_client()
    interactions.register(
        InteractionSpec(interaction_id="int_ageing", delivered_offset=timedelta(hours=23))
    )
    assert (
        client.post(
            "/api/v1/interactions/int_ageing/rating",
            json={"rating": "up"},
            headers=LEARNER_HEADERS,
        ).status_code
        == 201
    )

    # The mock keeps 'delivered 23 hours ago' true relative to the clock, so re-register
    # the scenario with a fixed delivery time and then move the clock past the window.
    interactions.register(
        InteractionSpec(interaction_id="int_ageing", delivered_offset=timedelta(hours=25))
    )
    late = client.post(
        "/api/v1/interactions/int_ageing/rating",
        json={"rating": "down"},
        headers=LEARNER_HEADERS,
    )
    assert late.status_code == 409


def test_a_configured_window_of_a_different_length_is_honoured(
    make_client, interactions, policy
):
    """The 24 hours is configuration, not a constant in the rating rule."""
    from uc10.adapters.memory.support import StaticThresholdConfigProvider

    client = make_client(
        policy_config=StaticThresholdConfigProvider(
            down_rate_threshold=0.30,
            minimum_sample_size=10,
            historical_rating_window_hours=48,
        )
    )
    assert (
        client.post(
            "/api/v1/interactions/int_delivered_25h/rating",
            json={"rating": "down"},
            headers=LEARNER_HEADERS,
        ).status_code
        == 201
    )
