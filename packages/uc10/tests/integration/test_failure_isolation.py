"""Feedback failures stay inside the feedback component.

Coaching is the product; feedback is peripheral.  A rating that cannot be saved returns a
retryable message and never becomes the caller's problem.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.conftest import LEARNER_HEADERS
from uc10.adapters.mock.faults import FailingRatingRepository
from uc10.application.results import RatingCaptureStatus
from uc10.domain.enums import RatingValue
from uc10.domain.models import RatingRecord
from uc10.ports.errors import ProviderTimeout, ProviderUnavailable


class ExplodingRatingRepository:
    """A collaborator that fails in a way no port contract allows."""

    def save(self, rating: RatingRecord) -> RatingRecord:
        raise RuntimeError("BOOM: an unexpected defect inside the feedback component")

    def for_interaction(self, interaction_id: str) -> list[RatingRecord]:
        raise RuntimeError("BOOM")

    def supersede(self, rating_id: str, by: str) -> RatingRecord:
        raise RuntimeError("BOOM")

    def current_in_window(self, window_start: datetime, window_end: datetime):
        raise RuntimeError("BOOM")


def coaching_main_path(feedback, *, interaction_id: str, user_id: str | None) -> dict:
    """Stands in for a caller whose main path is delivering coaching.

    It renders its response first and then offers the rating control. Nothing the
    feedback component does may stop it returning the coaching payload.
    """
    delivered = {"chat_visible": True, "response": "coaching delivered"}
    outcome = feedback.capture(
        interaction_id=interaction_id, user_id=user_id, rating=RatingValue.DOWN
    )
    delivered["feedback_saved"] = outcome.ok
    delivered["feedback_message"] = outcome.message
    delivered["feedback_retryable"] = outcome.retryable
    return delivered


# ------------------------------------------------------------- write failures


def test_a_rating_write_failure_returns_a_retryable_message(
    make_client, ratings_repository, flag_repository
):
    client = make_client(
        ratings_repository=FailingRatingRepository(ratings_repository, fail_saves=1)
    )
    response = client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down"},
        headers=LEARNER_HEADERS,
    )
    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "failed_retryable"
    assert body["retryable"] is True
    assert "try again" in body["message"].lower()
    assert ratings_repository.count() == 0


def test_the_retry_succeeds(make_client, ratings_repository):
    client = make_client(
        ratings_repository=FailingRatingRepository(ratings_repository, fail_saves=1)
    )
    first = client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down"},
        headers=LEARNER_HEADERS,
    )
    retry = client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down"},
        headers=LEARNER_HEADERS,
    )
    assert (first.status_code, retry.status_code) == (503, 201)
    assert ratings_repository.count() == 1


def test_a_write_failure_response_carries_no_learner_content(make_client, ratings_repository):
    client = make_client(
        ratings_repository=FailingRatingRepository(ratings_repository, fail_saves=1)
    )
    response = client.post(
        "/api/v1/interactions/int_answer/rating",
        json={"rating": "down", "comment": "CANARY_COMMENT_TEXT_DO_NOT_LOG my client matter"},
        headers=LEARNER_HEADERS,
    )
    assert "CANARY_COMMENT_TEXT_DO_NOT_LOG" not in response.text
    assert "MOCK_RESPONSE_TEXT_DO_NOT_LOG" not in response.text


def test_an_unavailable_interaction_provider_is_retryable_and_writes_nothing(
    client, ratings_repository
):
    response = client.post(
        "/api/v1/interactions/int_unavailable/rating",
        json={"rating": "up"},
        headers=LEARNER_HEADERS,
    )
    assert response.status_code == 503
    assert response.json()["error"]["retryable"] is True
    assert ratings_repository.count() == 0


def test_a_provider_timeout_is_retryable(client):
    response = client.post(
        "/api/v1/interactions/int_timeout/rating", json={"rating": "up"}, headers=LEARNER_HEADERS
    )
    assert response.status_code == 503
    assert response.json()["error"]["retryable"] is True


def test_an_unmappable_upstream_response_is_not_presented_as_retryable(client):
    response = client.post(
        "/api/v1/interactions/int_invalid/rating", json={"rating": "up"}, headers=LEARNER_HEADERS
    )
    assert response.status_code == 502
    assert response.json()["error"]["retryable"] is False


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailable("X", "upstream_unavailable"),
        ProviderTimeout("X", "upstream_timeout"),
    ],
)
def test_no_upstream_error_text_or_provider_name_crosses_the_boundary(error):
    rendered = str(error)
    assert "http" not in rendered.lower()
    assert "traceback" not in rendered.lower()
    assert rendered.count(":") == 1


# ------------------------------------------------------ main-path isolation


def test_a_port_failure_cannot_propagate_into_the_callers_main_path(
    make_container, ratings_repository
):
    container = make_container(
        ratings_repository=FailingRatingRepository(ratings_repository, fail_saves=99)
    )
    result = coaching_main_path(
        container.feedback, interaction_id="int_answer", user_id="user_alice"
    )
    assert result["chat_visible"] is True
    assert result["response"] == "coaching delivered"
    assert result["feedback_saved"] is False
    assert result["feedback_retryable"] is True


def test_an_unexpected_defect_cannot_propagate_into_the_callers_main_path(make_container):
    """Not a port error -- a plain bug. The facade still returns a result."""
    container = make_container(ratings_repository=ExplodingRatingRepository())
    result = coaching_main_path(
        container.feedback, interaction_id="int_answer", user_id="user_alice"
    )
    assert result["chat_visible"] is True
    assert result["feedback_saved"] is False
    assert result["feedback_retryable"] is True


def test_an_unexpected_defect_on_read_returns_no_rating_rather_than_raising(make_container):
    container = make_container(ratings_repository=ExplodingRatingRepository())
    assert (
        container.feedback.current_rating(interaction_id="int_answer", user_id="user_alice") is None
    )


def test_the_facade_never_raises_for_any_scenario_in_the_mock_table(make_container):
    container = make_container()
    scenarios = [
        "int_answer",
        "int_redirect",
        "int_refusal",
        "int_clarifying",
        "int_degraded",
        "int_unknown_category",
        "int_delivered_23h",
        "int_delivered_25h",
        "int_other_learner",
        "int_unavailable",
        "int_timeout",
        "int_invalid",
        "int_does_not_exist",
    ]
    for interaction_id in scenarios:
        for user_id in ("user_alice", None):
            outcome = container.feedback.capture(
                interaction_id=interaction_id, user_id=user_id, rating=RatingValue.DOWN
            )
            assert isinstance(outcome.status, RatingCaptureStatus)
            assert outcome.message


def test_a_flag_evaluation_defect_cannot_fail_a_rating(make_container, ratings_repository):
    container = make_container()

    def exploding_evaluation(topic_tag: str) -> None:
        raise RuntimeError("BOOM inside flag evaluation")

    container.ratings._on_rating_recorded = exploding_evaluation

    outcome = container.feedback.capture(
        interaction_id="int_answer", user_id="user_alice", rating=RatingValue.DOWN
    )
    assert outcome.ok is True
    assert ratings_repository.count() == 1


def test_a_supersede_failure_still_leaves_the_newest_rating_authoritative(
    make_container, ratings_repository
):
    container = make_container(
        ratings_repository=FailingRatingRepository(ratings_repository, fail_supersedes=99)
    )
    first = container.feedback.capture(
        interaction_id="int_answer", user_id="user_alice", rating=RatingValue.DOWN
    )
    container.clock.advance(minutes=1)
    second = container.feedback.capture(
        interaction_id="int_answer", user_id="user_alice", rating=RatingValue.UP
    )
    assert first.ok and second.ok
    current = container.feedback.current_rating(
        interaction_id="int_answer", user_id="user_alice"
    )
    assert current is not None
    assert current.rating is RatingValue.UP
    assert ratings_repository.count() == 2
