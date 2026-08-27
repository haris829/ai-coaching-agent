"""The fixed platform contract: enums, NARIC handling, profiles, record schemas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from uc10.domain.enums import (
    ExplanationProfile,
    FlagStatus,
    NaricLevel,
    NaricLevelSource,
    RatingValue,
    ResponseCategory,
    SourceStatus,
    explanation_profile_for,
)
from uc10.domain.models import (
    ASSUMED_FLAG_FIELDS,
    REQUIRED_FLAG_FIELDS,
    REQUIRED_RATING_FIELDS,
    ContentReviewFlag,
    InteractionRecord,
    RatingRecord,
)
from uc10.domain.naric import normalise_naric_level
from uc10.domain.window import Window

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ vocabularies


def test_naric_levels_are_the_closed_specified_set():
    assert [level.name for level in NaricLevel] == [
        "LEVEL_3",
        "LEVEL_4",
        "LEVEL_5",
        "LEVEL_6",
        "LEVEL_7",
        "LEVEL_7_PLUS",
    ]


def test_every_emitted_enum_value_is_lowercase():
    for enum_cls in (
        NaricLevel,
        NaricLevelSource,
        ExplanationProfile,
        SourceStatus,
        RatingValue,
        FlagStatus,
        ResponseCategory,
    ):
        for member in enum_cls:
            assert member.value == member.value.lower()


def test_source_status_vocabulary_is_complete_and_distinguishes_empty_from_unavailable():
    assert {s.value for s in SourceStatus} == {
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
def test_explanation_profile_mapping(level, profile):
    assert explanation_profile_for(level) is profile


# ----------------------------------------------------------------- NARIC values


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("LEVEL_7_PLUS", NaricLevel.LEVEL_7_PLUS),
        ("level_7_plus", NaricLevel.LEVEL_7_PLUS),
        ("Level 7 Plus", NaricLevel.LEVEL_7_PLUS),
        ("level-7+", NaricLevel.LEVEL_7_PLUS),
        ("LEVEL_3", NaricLevel.LEVEL_3),
        (NaricLevel.LEVEL_6, NaricLevel.LEVEL_6),
    ],
)
def test_recognised_values_are_retrieved(raw, expected):
    result = normalise_naric_level(raw)
    assert result.level is expected
    assert result.source is NaricLevelSource.RETRIEVED
    assert result.status is SourceStatus.AVAILABLE


@pytest.mark.parametrize("raw", [7, 7.0, True, "advanced", "high", "EQF 7", "level_9", "??"])
def test_an_unmappable_value_is_an_invalid_response_not_a_level(raw):
    """Including an integer scale, which this platform never uses."""
    result = normalise_naric_level(raw)
    assert result.level is NaricLevel.LEVEL_5
    assert result.source is NaricLevelSource.DEFAULT
    assert result.status is SourceStatus.INVALID


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_a_missing_value_is_empty_not_invalid_and_not_unavailable(raw):
    result = normalise_naric_level(raw)
    assert result.level is NaricLevel.LEVEL_5
    assert result.source is NaricLevelSource.DEFAULT
    assert result.status is SourceStatus.EMPTY


def test_a_defaulted_level_still_resolves_an_explanation_profile():
    assert normalise_naric_level(7).explanation_profile is ExplanationProfile.INTERMEDIATE


# --------------------------------------------------------------- record schemas


def _rating(**overrides) -> RatingRecord:
    payload = {
        "rating_id": "rat_1",
        "interaction_id": "int_1",
        "session_id": "sess_1",
        "user_id": "user_1",
        "rating": RatingValue.DOWN,
        "comment": None,
        "question_text": "q",
        "response_text": "r",
        "naric_level": NaricLevel.LEVEL_7,
        "session_mode": "coaching",
        "topic_tag": "contract_formation",
        "rated_at": NOW,
        "superseded_by": None,
    }
    payload.update(overrides)
    return RatingRecord(**payload)


def test_rating_record_field_set_is_exactly_the_specified_metadata_set():
    assert set(RatingRecord.model_fields) == set(REQUIRED_RATING_FIELDS)


def test_flag_record_carries_every_specified_field():
    assert set(ContentReviewFlag.model_fields) >= REQUIRED_FLAG_FIELDS
    extra = set(ContentReviewFlag.model_fields) - REQUIRED_FLAG_FIELDS
    assert extra == set(ASSUMED_FLAG_FIELDS), "undocumented field added to the flag record"


def test_a_flag_record_has_no_field_that_could_hold_learner_content():
    for field in ("question_text", "response_text", "comment"):
        assert field not in ContentReviewFlag.model_fields


def test_records_reject_unknown_fields_and_naive_timestamps():
    with pytest.raises(ValidationError):
        _rating(unexpected="value")
    with pytest.raises(ValidationError):
        _rating(rated_at=datetime(2026, 6, 1, 12, 0))


def test_superseding_returns_a_new_record_and_never_mutates_the_original():
    original = _rating()
    superseded = original.superseded("rat_2")
    assert original.superseded_by is None
    assert original.is_current is True
    assert superseded.superseded_by == "rat_2"
    assert superseded.is_current is False


def test_course_completion_percentage_is_an_integer_0_to_100():
    def build(percent):
        return InteractionRecord(
            interaction_id="int_1",
            session_id="sess_1",
            user_id="user_1",
            question_text="q",
            response_text="r",
            response_category=ResponseCategory.ANSWER,
            topic_tag="contract_formation",
            session_mode="coaching",
            naric_level=NaricLevel.LEVEL_7,
            naric_level_source=NaricLevelSource.RETRIEVED,
            explanation_profile=ExplanationProfile.ADVANCED,
            naric_source_status=SourceStatus.AVAILABLE,
            course_completion_percent=percent,
            delivered_at=NOW,
            source_status=SourceStatus.AVAILABLE,
        )

    assert build(0).course_completion_percent == 0
    assert build(100).course_completion_percent == 100
    assert build(None).course_completion_percent is None
    for invalid in (-1, 101, 40.5):
        with pytest.raises(ValidationError):
            build(invalid)


# --------------------------------------------------------------------- window


def test_rolling_window_covers_the_configured_number_of_days():
    window = Window.rolling(NOW, 7)
    assert (window.end - window.start).days == 7
    assert window.contains(NOW)
    assert window.contains(window.start)
    assert not window.contains(NOW - timedelta(days=8))
    assert not window.contains(NOW + timedelta(seconds=1))


def test_windows_overlap_check():
    first = Window.rolling(NOW, 7)
    later = Window.rolling(NOW + timedelta(days=3), 7)
    assert first.overlaps(later)
