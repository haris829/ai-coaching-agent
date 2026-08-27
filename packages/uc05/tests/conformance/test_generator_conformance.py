"""GuidingQuestionGenerator and AnswerGenerator conformance.

    python -m pytest tests/conformance/test_generator_conformance.py -q
"""

from __future__ import annotations

import pytest

from uc05.application.guards import GuidingQuestionGuard
from uc05.domain.enums import (
    DialogueState,
    ExplanationProfile,
    NaricLevel,
    NaricLevelSource,
)
from uc05.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc05.domain.models import (
    Dialogue,
    FourPartAnswer,
    GuidingQuestionResult,
    LearnerContext,
    utcnow,
)

from .harness import ANSWER_HARNESSES, GUIDING_HARNESSES, ids
from .shared import (
    assert_honours_timeout,
    assert_no_leak,
    assert_raises_category,
    skip_unless,
)

QUESTION = "When is a contract formed, and what does consideration require?"


def a_context() -> LearnerContext:
    return LearnerContext(
        naric_level=NaricLevel.LEVEL_5,
        naric_level_source=NaricLevelSource.RETRIEVED,
        practice_area="Employment",
        source_status={},
    )


def a_dialogue() -> Dialogue:
    now = utcnow()
    return Dialogue(
        dialogue_id="d-conformance",
        session_id="s-conformance",
        user_id="u-conformance",
        question_text=QUESTION,
        topic_tag="contract",
        naric_level=NaricLevel.LEVEL_5,
        naric_level_source=NaricLevelSource.RETRIEVED,
        explanation_profile=ExplanationProfile.INTERMEDIATE,
        practice_area="Employment",
        source_status={},
        state=DialogueState.AWAITING_LEARNER_RESPONSE,
        exchange_cap=5,
        prompt_version="socratic-v1.2.0",
        created_at=now,
        updated_at=now,
    )


# --------------------------------------------------------------------------
# GuidingQuestionGenerator
# --------------------------------------------------------------------------

guiding = pytest.mark.parametrize(
    "harness", GUIDING_HARNESSES, ids=ids(GUIDING_HARNESSES)
)


@guiding
async def test_guiding_returns_the_platform_type(harness):
    result = await harness.happy().generate(a_dialogue(), QUESTION, a_context())
    assert isinstance(result, GuidingQuestionResult)
    assert result.question.strip()
    assert result.probing_focus.strip()


@guiding
async def test_guiding_output_satisfies_the_output_guard(harness):
    """Whatever the upstream returns must survive UC-05's own rejection rules."""
    result = await harness.happy().generate(a_dialogue(), QUESTION, a_context())
    GuidingQuestionGuard().validate(result, a_dialogue())


@guiding
async def test_guiding_records_the_prompt_version_it_was_given(harness):
    dialogue = a_dialogue()
    result = await harness.happy().generate(dialogue, QUESTION, a_context())
    assert result.prompt_version == dialogue.prompt_version


@guiding
async def test_guiding_leaks_no_upstream_shape(harness):
    result = await harness.happy().generate(a_dialogue(), QUESTION, a_context())
    assert_no_leak(result.model_dump(), harness)


@guiding
@pytest.mark.parametrize(
    "state,expected",
    [
        ("unavailable", ProviderUnavailable),
        ("timeout", ProviderTimeout),
        ("malformed", ProviderInvalidResponse),
    ],
)
async def test_guiding_failure_modes_raise_the_right_category(harness, state, expected):
    adapter = skip_unless(getattr(harness, state), state)
    await assert_raises_category(
        lambda: adapter.generate(a_dialogue(), QUESTION, a_context()), expected, harness
    )


@guiding
async def test_guiding_honours_the_caller_budget(harness):
    adapter = skip_unless(harness.slow, "slow")
    await assert_honours_timeout(
        lambda: adapter.generate(a_dialogue(), QUESTION, a_context())
    )


# --------------------------------------------------------------------------
# AnswerGenerator
# --------------------------------------------------------------------------

answers = pytest.mark.parametrize("harness", ANSWER_HARNESSES, ids=ids(ANSWER_HARNESSES))


@answers
async def test_answer_returns_the_platform_type(harness):
    result = await harness.happy().generate(QUESTION, a_context())
    assert isinstance(result, FourPartAnswer)


@answers
async def test_answer_has_all_four_parts_non_blank(harness):
    result = await harness.happy().generate(QUESTION, a_context())
    parts = result.model_dump()
    assert set(parts) == {
        "plain_english_explanation",
        "formal_legal_definition",
        "practical_example",
        "authority_reference",
    }
    assert all(value.strip() for value in parts.values())


@answers
async def test_answer_leaks_no_upstream_shape(harness):
    result = await harness.happy().generate(QUESTION, a_context())
    assert_no_leak(result.model_dump(), harness)


@answers
@pytest.mark.parametrize(
    "state,expected",
    [
        ("unavailable", ProviderUnavailable),
        ("timeout", ProviderTimeout),
        ("malformed", ProviderInvalidResponse),
    ],
)
async def test_answer_failure_modes_raise_the_right_category(harness, state, expected):
    adapter = skip_unless(getattr(harness, state), state)
    await assert_raises_category(
        lambda: adapter.generate(QUESTION, a_context()), expected, harness
    )


@answers
async def test_a_missing_part_is_invalid_never_a_partial_answer(harness):
    adapter = skip_unless(harness.empty, "empty")
    await assert_raises_category(
        lambda: adapter.generate(QUESTION, a_context()),
        ProviderInvalidResponse,
        harness,
    )


@answers
async def test_answer_honours_the_caller_budget(harness):
    adapter = skip_unless(harness.slow, "slow")
    await assert_honours_timeout(lambda: adapter.generate(QUESTION, a_context()))
