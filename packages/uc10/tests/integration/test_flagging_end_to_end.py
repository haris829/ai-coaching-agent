"""Content review flagging through the application service and the API."""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN_HEADERS, FIXED_NOW
from tests.factories import TOPIC, rating_set
from tests.helpers import seed_repository, seed_via_api
from uc10.adapters.memory.repositories import InMemoryFlagWorkQueue
from uc10.adapters.memory.support import (
    SettingsThresholdConfigProvider,
    StaticThresholdConfigProvider,
)
from uc10.application.results import FlagWriteStatus
from uc10.config import reset_settings_cache
from uc10.domain.enums import RatingValue
from uc10.domain.flagging import DecisionReason


@pytest.fixture
def build_flagger(ratings_repository, flag_repository, notifications, clock):
    def _build(*, threshold: float = 0.30, minimum: int = 10, config=None):
        from uc10.application.flagging_service import FlaggingService

        return FlaggingService(
            ratings=ratings_repository,
            flags=flag_repository,
            work_queue=InMemoryFlagWorkQueue(now_factory=clock.now),
            notifications=notifications,
            config=config
            or StaticThresholdConfigProvider(
                down_rate_threshold=threshold, minimum_sample_size=minimum
            ),
            clock=clock,
        )

    return _build


# ------------------------------------------------------------ rate boundaries


@pytest.mark.parametrize(
    ("downs", "total", "should_flag"), [(29, 100, False), (30, 100, True), (31, 100, True)]
)
def test_flag_creation_at_the_29_30_and_31_percent_boundaries(
    build_flagger, ratings_repository, flag_repository, downs, total, should_flag
):
    seed_repository(ratings_repository, rating_set(total=total, downs=downs, now=FIXED_NOW))
    flagger = build_flagger()

    result = flagger.evaluate_topic(TOPIC)

    if should_flag:
        assert result.write_status is FlagWriteStatus.CREATED
        flag = result.flag
        assert flag is not None
        assert flag.down_ratings == downs
        assert flag.total_ratings == total
        assert flag.threshold_applied == 0.30
        assert len(flag_repository.list_open()) == 1
    else:
        assert result.write_status is FlagWriteStatus.NOT_REQUIRED
        assert result.decision.reason is DecisionReason.BELOW_THRESHOLD
        assert flag_repository.list_open() == []


@pytest.mark.parametrize("minimum", [1, 5, 20])
def test_flag_creation_around_the_minimum_sample_size(
    build_flagger, ratings_repository, flag_repository, minimum
):
    """A 100% down rate on a sample below the minimum raises nothing."""
    seed_repository(
        ratings_repository, rating_set(total=minimum - 1, downs=minimum - 1, now=FIXED_NOW)
    )
    flagger = build_flagger(minimum=minimum)

    if minimum > 1:
        below = flagger.evaluate_topic(TOPIC)
        assert below.decision.down_rate == pytest.approx(1.0)
        assert below.write_status is FlagWriteStatus.NOT_REQUIRED
        assert below.decision.reason is DecisionReason.BELOW_MINIMUM_SAMPLE
        assert flag_repository.list_open() == []

    seed_repository(
        ratings_repository,
        rating_set(total=1, downs=1, now=FIXED_NOW, start_index=500),
    )
    above = flagger.evaluate_topic(TOPIC)
    assert above.write_status is FlagWriteStatus.CREATED
    assert above.flag is not None
    assert above.flag.minimum_sample_size_applied == minimum


def test_the_configured_threshold_changes_behaviour_with_no_code_change(
    build_flagger, ratings_repository, flag_repository, monkeypatch
):
    """Same dataset, same code, different configured threshold."""
    seed_repository(ratings_repository, rating_set(total=100, downs=40, now=FIXED_NOW))

    monkeypatch.setenv("FLAG_DOWN_RATE_THRESHOLD", "0.30")
    monkeypatch.setenv("FLAG_MINIMUM_SAMPLE_SIZE", "10")
    reset_settings_cache()
    at_thirty = build_flagger(config=SettingsThresholdConfigProvider()).evaluate_topic(TOPIC)
    assert at_thirty.write_status is FlagWriteStatus.CREATED
    assert at_thirty.flag is not None
    assert at_thirty.flag.threshold_applied == 0.30

    monkeypatch.setenv("FLAG_DOWN_RATE_THRESHOLD", "0.50")
    reset_settings_cache()
    at_fifty = build_flagger(config=SettingsThresholdConfigProvider()).evaluate_topic("audit")
    assert at_fifty.write_status is FlagWriteStatus.NOT_REQUIRED

    # ...and the same topic, re-evaluated under the stricter rule, is below threshold.
    seed_repository(
        ratings_repository, rating_set(total=10, downs=4, now=FIXED_NOW, topic_tag="audit")
    )
    stricter = build_flagger(config=SettingsThresholdConfigProvider()).evaluate_topic("audit")
    assert stricter.decision.reason is DecisionReason.BELOW_THRESHOLD


def test_the_configured_minimum_sample_size_changes_behaviour_with_no_code_change(
    build_flagger, ratings_repository, monkeypatch
):
    seed_repository(ratings_repository, rating_set(total=3, downs=3, now=FIXED_NOW))

    monkeypatch.setenv("FLAG_MINIMUM_SAMPLE_SIZE", "10")
    reset_settings_cache()
    assert (
        build_flagger(config=SettingsThresholdConfigProvider()).evaluate_topic(TOPIC).write_status
        is FlagWriteStatus.NOT_REQUIRED
    )

    monkeypatch.setenv("FLAG_MINIMUM_SAMPLE_SIZE", "3")
    reset_settings_cache()
    assert (
        build_flagger(config=SettingsThresholdConfigProvider()).evaluate_topic(TOPIC).write_status
        is FlagWriteStatus.CREATED
    )


def test_the_shipped_default_policy_is_thirty_percent_and_a_minimum_sample_of_ten(monkeypatch):
    for key in ("FLAG_DOWN_RATE_THRESHOLD", "FLAG_MINIMUM_SAMPLE_SIZE", "FLAG_WINDOW_DAYS"):
        monkeypatch.delenv(key, raising=False)
    reset_settings_cache()
    config = SettingsThresholdConfigProvider()
    assert config.down_rate_threshold() == 0.30
    assert config.minimum_sample_size() == 10  # ASSUMED BY US -- needs company confirmation
    assert config.window_days() == 7
    assert config.historical_rating_window_hours() == 24


# ------------------------------------------------------------- rolling window


def test_the_rate_is_computed_over_the_rolling_window_across_all_users(
    build_flagger, ratings_repository
):
    from datetime import timedelta

    seed_repository(ratings_repository, rating_set(total=10, downs=10, now=FIXED_NOW))
    seed_repository(
        ratings_repository,
        rating_set(total=200, downs=0, now=FIXED_NOW - timedelta(days=9)),
    )
    result = build_flagger().evaluate_topic(TOPIC)
    assert result.decision.total_ratings == 10
    assert result.write_status is FlagWriteStatus.CREATED
    assert result.flag is not None
    assert (result.flag.window_end - result.flag.window_start).days == 7


def test_each_topic_is_flagged_independently(build_flagger, ratings_repository, flag_repository):
    seed_repository(ratings_repository, rating_set(total=10, downs=10, now=FIXED_NOW))
    seed_repository(
        ratings_repository,
        rating_set(total=10, downs=0, now=FIXED_NOW, topic_tag="professional_conduct"),
    )
    report = build_flagger().run_cycle()
    assert len(report.created) == 1
    assert [flag.topic_tag for flag in flag_repository.list_open()] == [TOPIC]


# ---------------------------------------------------------------- through HTTP


def test_a_flag_raised_by_ratings_posted_through_the_api_is_visible_to_the_platform_team(
    client, interactions, notifications
):
    """End to end: ten learners rate one topic, three thumbs down -- exactly 30%."""
    seed_via_api(
        client, interactions, total=10, downs=3, topic_tag="undue_influence", with_comments=True
    )

    listing = client.get("/api/v1/admin/flags", headers=ADMIN_HEADERS)
    assert listing.status_code == 200
    body = listing.json()
    assert body["count"] == 1
    flag = body["flags"][0]
    assert flag["topic_tag"] == "undue_influence"
    assert (flag["total_ratings"], flag["down_ratings"]) == (10, 3)
    assert flag["down_rate"] == pytest.approx(0.30)
    assert flag["threshold_applied"] == 0.30
    assert flag["minimum_sample_size_applied"] == 10
    assert len(flag["flagging_interaction_ids"]) == 3
    assert flag["status"] == "open"
    assert [f.flag_id for f in notifications.notified] == [flag["flag_id"]]


def test_ratings_posted_through_the_api_below_the_threshold_raise_nothing(
    client, interactions, notifications
):
    seed_via_api(client, interactions, total=10, downs=2, topic_tag="mortgage_fraud")
    listing = client.get("/api/v1/admin/flags", headers=ADMIN_HEADERS)
    assert listing.json() == {"flags": [], "count": 0}
    assert notifications.notified == []


def test_a_flag_carries_no_question_response_or_comment_text(client, interactions):
    seed_via_api(
        client, interactions, total=10, downs=10, topic_tag="client_confidentiality",
        with_comments=True,
    )
    body = client.get("/api/v1/admin/flags", headers=ADMIN_HEADERS).text
    for fragment in (
        "MOCK_QUESTION_TEXT_DO_NOT_LOG",
        "MOCK_RESPONSE_TEXT_DO_NOT_LOG",
        "CANARY_COMMENT_TEXT_DO_NOT_LOG",
    ):
        assert fragment not in body


def test_a_learner_flipping_to_thumbs_up_can_take_a_topic_back_below_the_threshold(
    client, interactions, container
):
    ids = seed_via_api(client, interactions, total=10, downs=3, topic_tag="tort_negligence")
    assert len(container.flag_repository.list_open()) == 1

    # The learner who rated interaction 0 changes their mind: 3 downs become 2.
    flipped = client.post(
        f"/api/v1/interactions/{ids[0]}/rating",
        json={"rating": RatingValue.UP.value},
        headers={"X-User-Id": "user_seed_0"},
    )
    assert flipped.status_code == 200

    decision = container.flagging.evaluate_topic("tort_negligence").decision
    assert (decision.total_ratings, decision.down_ratings) == (10, 2)
    assert decision.reason is DecisionReason.BELOW_THRESHOLD
    # The existing flag is not retracted automatically -- a raised flag stays for the team.
    assert len(container.flag_repository.list_open()) == 1
