"""The flagging rule, tested where it is right or wrong: at the boundaries."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import FIXED_NOW
from tests.factories import TOPIC, rating, rating_set
from uc10.domain.enums import RatingValue
from uc10.domain.flagging import DecisionReason, FlaggingPolicy, evaluate_topic, topics_in
from uc10.domain.window import Window

WINDOW = Window.rolling(FIXED_NOW, 7)


def decide(ratings, *, threshold: float, minimum: int):
    return evaluate_topic(
        topic_tag=TOPIC,
        ratings=ratings,
        window=WINDOW,
        policy=FlaggingPolicy(down_rate_threshold=threshold, minimum_sample_size=minimum),
        evaluated_at=FIXED_NOW,
    )


# --------------------------------------------------------------- rate boundary


@pytest.mark.parametrize(
    ("downs", "total", "expected_rate", "should_flag"),
    [
        (29, 100, 0.29, False),  # just below the threshold
        (30, 100, 0.30, True),   # exactly on the threshold
        (31, 100, 0.31, True),   # just above the threshold
    ],
)
def test_down_rate_boundary_at_29_30_and_31_percent(downs, total, expected_rate, should_flag):
    decision = decide(
        rating_set(total=total, downs=downs, now=FIXED_NOW), threshold=0.30, minimum=10
    )
    assert decision.down_rate == pytest.approx(expected_rate)
    assert decision.flagged is should_flag
    if not should_flag:
        assert decision.reason is DecisionReason.BELOW_THRESHOLD


def test_exactly_on_the_threshold_flags_for_awkward_fractions_too():
    """3/10 is 0.3 in decimal and 0.2999... in binary. It must still flag."""
    decision = decide(rating_set(total=10, downs=3, now=FIXED_NOW), threshold=0.30, minimum=10)
    assert decision.flagged is True


def test_a_changed_threshold_changes_the_decision_with_no_code_change():
    ratings = rating_set(total=100, downs=40, now=FIXED_NOW)
    assert decide(ratings, threshold=0.30, minimum=10).flagged is True
    assert decide(ratings, threshold=0.50, minimum=10).flagged is False


# ------------------------------------------------------- sample-size boundary


@pytest.mark.parametrize("minimum", [1, 5, 20])
def test_sample_size_boundary_just_below_and_just_above_the_minimum(minimum):
    """A 100% down rate below the minimum sample does not flag. One more rating does."""
    below = decide(
        rating_set(total=minimum - 1, downs=minimum - 1, now=FIXED_NOW),
        threshold=0.30,
        minimum=minimum,
    )
    at = decide(
        rating_set(total=minimum, downs=minimum, now=FIXED_NOW), threshold=0.30, minimum=minimum
    )
    if minimum > 1:
        assert below.down_rate == pytest.approx(1.0)
        assert below.flagged is False
        assert below.reason is DecisionReason.BELOW_MINIMUM_SAMPLE
    assert at.flagged is True


def test_a_single_thumbs_down_does_not_raise_a_flag_on_a_sample_of_one():
    """The gap the specification leaves: without a minimum sample, this flags at 100%."""
    one_unhappy_learner = [rating(value=RatingValue.DOWN, rated_at=FIXED_NOW)]

    with_minimum = decide(one_unhappy_learner, threshold=0.30, minimum=10)
    assert with_minimum.down_rate == pytest.approx(1.0)
    assert with_minimum.flagged is False
    assert with_minimum.reason is DecisionReason.BELOW_MINIMUM_SAMPLE

    # Evidence for the assumptions register: with the minimum set to 1, the same single
    # rating raises a content review flag against a legal topic.
    without_minimum = decide(one_unhappy_learner, threshold=0.30, minimum=1)
    assert without_minimum.flagged is True


# ------------------------------------------------------------------- contents


def test_a_flag_candidate_carries_counts_rate_rule_and_flagging_interactions():
    ratings = rating_set(total=20, downs=8, now=FIXED_NOW)
    candidate = decide(ratings, threshold=0.30, minimum=10).candidate
    assert candidate is not None
    assert (candidate.total_ratings, candidate.down_ratings) == (20, 8)
    assert candidate.down_rate == pytest.approx(0.4)
    assert candidate.threshold_applied == 0.30
    assert candidate.minimum_sample_size_applied == 10
    expected = {r.interaction_id for r in ratings if r.rating is RatingValue.DOWN}
    assert set(candidate.flagging_interaction_ids) == expected
    assert len(candidate.flagging_interaction_ids) == 8


def test_a_flag_candidate_carries_no_learner_content():
    candidate = decide(
        rating_set(total=20, downs=20, now=FIXED_NOW), threshold=0.30, minimum=10
    ).candidate
    assert candidate is not None
    serialised = repr(candidate)
    assert "MOCK_QUESTION_TEXT_DO_NOT_LOG" not in serialised
    assert "MOCK_RESPONSE_TEXT_DO_NOT_LOG" not in serialised
    assert "CANARY_COMMENT_TEXT_DO_NOT_LOG" not in serialised


# --------------------------------------------------------------------- window


def test_ratings_outside_the_rolling_window_are_not_counted():
    inside = rating_set(total=10, downs=10, now=FIXED_NOW)
    outside = rating_set(total=50, downs=0, now=FIXED_NOW - timedelta(days=8))
    decision = decide(inside + outside, threshold=0.30, minimum=10)
    assert decision.total_ratings == 10
    assert decision.flagged is True


def test_other_topics_do_not_contribute_to_a_topics_rate():
    mine = rating_set(total=10, downs=10, now=FIXED_NOW)
    theirs = rating_set(total=50, downs=0, now=FIXED_NOW, topic_tag="professional_conduct")
    decision = decide(mine + theirs, threshold=0.30, minimum=10)
    assert decision.total_ratings == 10
    assert decision.down_ratings == 10


def test_no_ratings_is_not_a_flag():
    decision = decide([], threshold=0.30, minimum=10)
    assert decision.reason is DecisionReason.NO_RATINGS
    assert decision.flagged is False


def test_topics_in_returns_each_topic_once_in_first_seen_order():
    ratings = rating_set(total=2, downs=1, now=FIXED_NOW) + rating_set(
        total=2, downs=1, now=FIXED_NOW, topic_tag="professional_conduct"
    )
    assert topics_in(ratings) == [TOPIC, "professional_conduct"]


def test_policy_rejects_nonsensical_values():
    with pytest.raises(ValueError):
        FlaggingPolicy(down_rate_threshold=1.5, minimum_sample_size=10)
    with pytest.raises(ValueError):
        FlaggingPolicy(down_rate_threshold=0.30, minimum_sample_size=0)
