"""The platform contract: closed vocabularies, lowercase values, exact types."""

from __future__ import annotations

import pytest

from uc08.domain.enums import (
    DEFAULT_NARIC_LEVEL,
    DeliveryStatus,
    ExplanationProfile,
    FreezeOfferStatus,
    NaricLevel,
    NaricLevelSource,
    PersistenceOutcome,
    SessionIdSource,
    SourceStatus,
    StreakOutcome,
)
from uc08.domain.models import Topic
from uc08.domain.naric import (
    explanation_profile_for,
    normalise_completion_percent,
    normalise_naric_level,
)

ALL_ENUMS = (
    NaricLevel,
    NaricLevelSource,
    ExplanationProfile,
    SourceStatus,
    StreakOutcome,
    FreezeOfferStatus,
    DeliveryStatus,
    PersistenceOutcome,
    SessionIdSource,
)


@pytest.mark.parametrize("enum_class", ALL_ENUMS)
def test_every_emitted_enum_value_is_lowercase(enum_class):
    for member in enum_class:
        assert member.value == member.value.lower(), member


def test_the_naric_scale_is_the_closed_platform_set():
    assert [member.value for member in NaricLevel] == [
        "level_3",
        "level_4",
        "level_5",
        "level_6",
        "level_7",
        "level_7_plus",
    ]
    # Not an integer scale, and not a three-point pedagogic scale.
    assert all(not member.value.isdigit() for member in NaricLevel)
    assert len(NaricLevel) == 6


def test_the_source_status_vocabulary_is_complete_and_distinct():
    assert {member.value for member in SourceStatus} == {
        "available",
        "empty",
        "partial",
        "unavailable",
        "invalid",
    }
    assert SourceStatus.EMPTY is not SourceStatus.UNAVAILABLE


@pytest.mark.parametrize(
    ("level", "profile"),
    [
        (NaricLevel.LEVEL_3, ExplanationProfile.BASIC),
        (NaricLevel.LEVEL_4, ExplanationProfile.BASIC),
        (NaricLevel.LEVEL_5, ExplanationProfile.INTERMEDIATE),
        (NaricLevel.LEVEL_6, ExplanationProfile.INTERMEDIATE),
        (NaricLevel.LEVEL_7, ExplanationProfile.ADVANCED),
        (NaricLevel.LEVEL_7_PLUS, ExplanationProfile.ADVANCED),
    ],
)
def test_the_explanation_profile_mapping(level, profile):
    assert explanation_profile_for(level) is profile


@pytest.mark.parametrize(
    ("raw", "level"),
    [
        ("level_3", NaricLevel.LEVEL_3),
        ("LEVEL_7_PLUS", NaricLevel.LEVEL_7_PLUS),
        ("Level Six", NaricLevel.LEVEL_6),
        ("level 4", NaricLevel.LEVEL_4),
        ("7+", NaricLevel.LEVEL_7_PLUS),
        (5, NaricLevel.LEVEL_5),
        (NaricLevel.LEVEL_6, NaricLevel.LEVEL_6),
    ],
)
def test_a_mappable_level_is_retrieved(raw, level):
    reading = normalise_naric_level(raw, port="gap_report")
    assert reading.level is level
    assert reading.source is NaricLevelSource.RETRIEVED
    assert reading.status is SourceStatus.AVAILABLE


@pytest.mark.parametrize("raw", ["banana", "level_9", "2", 9, True, 3.5, object()])
def test_an_unmappable_level_is_invalid_not_a_level(raw):
    reading = normalise_naric_level(raw, port="gap_report")
    assert reading.level is DEFAULT_NARIC_LEVEL is NaricLevel.LEVEL_5
    assert reading.source is NaricLevelSource.DEFAULT
    assert reading.status is SourceStatus.INVALID


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_an_absent_level_is_empty_not_invalid(raw):
    reading = normalise_naric_level(raw, port="gap_report")
    assert reading.level is NaricLevel.LEVEL_5
    assert reading.source is NaricLevelSource.DEFAULT
    assert reading.status is SourceStatus.EMPTY


def test_an_unmappable_level_is_logged(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="uc08.domain.naric"):
        normalise_naric_level("postgraduate-ish", port="gap_report")

    record = next(item for item in caplog.records if item.getMessage() == "naric_level_invalid")
    assert record.naric_level_status == "invalid"
    assert record.applied_naric_level == "level_5"
    assert record.naric_level_source == "default"
    assert record.port == "gap_report"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, 0), (100, 100), (72, 72), ("72", 72), ("72%", 72), ("72.4 %", 72), (55.0, 55), (1.0, 1)],
)
def test_completion_percentage_is_an_integer_zero_to_one_hundred(raw, expected):
    reading = normalise_completion_percent(raw, port="gap_report")
    assert reading.percent == expected
    assert isinstance(reading.percent, int)
    assert reading.status is SourceStatus.AVAILABLE


@pytest.mark.parametrize("raw", [101, -1, "abc", "120%", 0.5, 0.64, True, object()])
def test_an_unusable_completion_value_is_invalid_and_never_guessed(raw):
    reading = normalise_completion_percent(raw, port="gap_report")
    assert reading.percent is None
    assert reading.status is SourceStatus.INVALID


def test_a_topic_rejects_a_percentage_outside_the_range():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Topic(
            topic_id="t",
            name="n",
            naric_level=NaricLevel.LEVEL_5,
            naric_level_source=NaricLevelSource.DEFAULT,
            naric_level_status=SourceStatus.EMPTY,
            explanation_profile=ExplanationProfile.INTERMEDIATE,
            course_progress_percent=101,
        )


def test_records_are_frozen_and_closed():
    from pydantic import ValidationError

    topic = Topic(
        topic_id="t",
        name="n",
        naric_level=NaricLevel.LEVEL_7,
        naric_level_source=NaricLevelSource.RETRIEVED,
        naric_level_status=SourceStatus.AVAILABLE,
        explanation_profile=ExplanationProfile.ADVANCED,
    )
    with pytest.raises(ValidationError):
        topic.name = "changed"
    with pytest.raises(ValidationError):
        Topic(
            topic_id="t",
            name="n",
            naric_level=NaricLevel.LEVEL_7,
            naric_level_source=NaricLevelSource.RETRIEVED,
            naric_level_status=SourceStatus.AVAILABLE,
            explanation_profile=ExplanationProfile.ADVANCED,
            unexpected="field",
        )


def test_the_streak_record_shape_is_exactly_the_platform_contract():
    from uc08.domain.models import StreakRecord

    assert list(StreakRecord.model_fields) == [
        "user_id",
        "current_streak_days",
        "longest_streak_days",
        "last_activity_at",
        "streak_started_at",
        "freeze_available",
        "freeze_used_at",
        "updated_at",
    ]


def test_the_badge_record_shape_is_exactly_the_platform_contract():
    from uc08.domain.models import Badge

    assert list(Badge.model_fields) == [
        "badge_id",
        "user_id",
        "milestone",
        "awarded_at",
        "question_count_at_award",
    ]


def test_timestamps_serialise_as_utc():
    from datetime import datetime, timezone

    from uc08.domain.models import Badge

    badge = Badge(
        badge_id="b",
        user_id="u",
        milestone=10,
        awarded_at=datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc),
        question_count_at_award=10,
    )
    assert badge.model_dump(mode="json")["awarded_at"].endswith("Z")
