"""NARIC level handling and depth calibration."""

from __future__ import annotations

import pytest

from conftest import IN_LESSON_QUESTION, build_harness
from uc04.adapters.mock import fixtures as fx
from uc04.adapters.mock.learner_context import MockLearnerContextProvider
from uc04.core.calibration import coerce_level, profile_for
from uc04.domain.enums import (
    ExplanationProfile,
    NARIC_LEVEL_PROFILE,
    NaricLevel,
    NaricLevelSource,
    SourceStatus,
)
from uc04.domain.models import LearnerContext


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (NaricLevel.LEVEL_3, ExplanationProfile.BASIC),
        (NaricLevel.LEVEL_4, ExplanationProfile.BASIC),
        (NaricLevel.LEVEL_5, ExplanationProfile.INTERMEDIATE),
        (NaricLevel.LEVEL_6, ExplanationProfile.INTERMEDIATE),
        (NaricLevel.LEVEL_7, ExplanationProfile.ADVANCED),
        (NaricLevel.LEVEL_7_PLUS, ExplanationProfile.ADVANCED),
    ],
)
def test_every_level_maps_to_the_expected_profile(level: NaricLevel, expected: ExplanationProfile) -> None:
    assert profile_for(level) is expected


def test_level_6_is_not_advanced() -> None:
    """An undergraduate law degree is not Masters level. Stated explicitly because it is the
    mapping most likely to be got wrong."""
    assert NARIC_LEVEL_PROFILE[NaricLevel.LEVEL_6] is ExplanationProfile.INTERMEDIATE


def test_all_six_levels_are_representable() -> None:
    assert len(NaricLevel) == 6
    assert {level.value for level in NaricLevel} == {
        "LEVEL_3", "LEVEL_4", "LEVEL_5", "LEVEL_6", "LEVEL_7", "LEVEL_7_PLUS",
    }


def _measure(text: str) -> dict[str, float]:
    words = text.split()
    sentence_count = max(1, text.count(".") + text.count("?"))
    return {
        "words": len(words),
        "mean_sentence_words": len(words) / sentence_count,
    }


def test_level_3_and_level_7_differ_on_measurable_properties() -> None:
    """Not merely a differing enum: the text itself changes in ways you can count."""
    basic = build_harness().ask(IN_LESSON_QUESTION, user_id=fx.USER_LEVEL_3)
    advanced = build_harness().ask(IN_LESSON_QUESTION, user_id=fx.USER_LEVEL_7)

    assert basic.naric_level is NaricLevel.LEVEL_3
    assert advanced.naric_level is NaricLevel.LEVEL_7
    assert basic.explanation_profile is ExplanationProfile.BASIC
    assert advanced.explanation_profile is ExplanationProfile.ADVANCED

    assert basic.explanation != advanced.explanation

    basic_metrics = _measure(basic.explanation)
    advanced_metrics = _measure(advanced.explanation)

    # Basic scaffolds: it offers to define unfamiliar terms.
    assert "unfamiliar" in basic.explanation.lower()
    assert "unfamiliar" not in advanced.explanation.lower()

    # Advanced carries the caveats a practitioner needs, and is denser for it.
    assert "caveats" in advanced.explanation.lower()
    assert "caveats" not in basic.explanation.lower()
    assert advanced_metrics["words"] > basic_metrics["words"]
    assert advanced_metrics["mean_sentence_words"] > basic_metrics["mean_sentence_words"]


def test_a_client_supplied_level_is_ignored() -> None:
    """Depth is derived server-side from the learner context, never from the request."""
    harness = build_harness()
    response = harness.service.ask(
        session_id=fx.SESSION_MAIN,
        user_id=fx.USER_LEVEL_3,
        course_id=fx.COURSE_EVIDENCE,
        lesson_id=fx.LESSON_HEARSAY,
        question=IN_LESSON_QUESTION,
    )
    # The service signature has no level parameter at all; this asserts the outcome.
    assert response.naric_level is NaricLevel.LEVEL_3
    assert response.explanation_profile is ExplanationProfile.BASIC


def test_retrieved_level_is_marked_retrieved() -> None:
    response = build_harness().ask(IN_LESSON_QUESTION, user_id=fx.USER_LEVEL_7)
    assert response.naric_level_source is NaricLevelSource.RETRIEVED


def test_missing_context_defaults_to_level_5_marked_default() -> None:
    response = build_harness().ask(IN_LESSON_QUESTION, user_id=fx.USER_NO_CONTEXT)
    assert response.naric_level is NaricLevel.LEVEL_5
    assert response.naric_level_source is NaricLevelSource.DEFAULT
    assert response.source_status["learner_context"] is SourceStatus.EMPTY
    assert response.explanation, "a missing context never means the learner gets no answer"


def test_unavailable_context_defaults_to_level_5_and_still_answers() -> None:
    response = build_harness().ask(IN_LESSON_QUESTION, user_id=fx.USER_CONTEXT_DOWN)
    assert response.naric_level is NaricLevel.LEVEL_5
    assert response.naric_level_source is NaricLevelSource.DEFAULT
    assert response.source_status["learner_context"] is SourceStatus.UNAVAILABLE
    assert response.explanation


def test_an_unmappable_upstream_level_is_invalid_not_a_level() -> None:
    response = build_harness().ask(IN_LESSON_QUESTION, user_id=fx.USER_LEVEL_INVALID)
    assert response.naric_level is NaricLevel.LEVEL_5
    assert response.naric_level_source is NaricLevelSource.DEFAULT
    assert response.source_status["learner_context"] is SourceStatus.INVALID
    assert response.explanation


def test_coerce_level_rejects_anything_that_is_not_a_level() -> None:
    assert coerce_level("LEVEL_7") is NaricLevel.LEVEL_7
    assert coerce_level("  LEVEL_3 ") is NaricLevel.LEVEL_3
    for bad in ("masters", "7", 7, None, "", "LEVEL_8", "level_7"):
        assert coerce_level(bad) is None, bad


def test_a_default_is_never_presented_as_retrieved() -> None:
    """The invariant, checked across every scenario the mock can produce."""
    provider = MockLearnerContextProvider()
    for user_id in (
        fx.USER_ENROLLED, fx.USER_LEVEL_3, fx.USER_LEVEL_7,
        fx.USER_NO_CONTEXT, fx.USER_LEVEL_INVALID,
    ):
        context: LearnerContext = provider.get_context(fx.SESSION_MAIN, user_id)
        if context.naric_level_source is NaricLevelSource.RETRIEVED:
            assert context.source_status in (SourceStatus.AVAILABLE, SourceStatus.PARTIAL)
        else:
            assert context.source_status in (SourceStatus.EMPTY, SourceStatus.UNAVAILABLE, SourceStatus.INVALID)
