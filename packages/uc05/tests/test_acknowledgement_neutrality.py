"""Section 5.3 -- acknowledgements are positive but neutral, never praise.

This is a specified constraint, not a stylistic preference, so it is tested
mechanically against an explicit list rather than by reading the strings.
"""

from __future__ import annotations

import pytest

from uc05.application.guards import GuidingQuestionGuard
from uc05.domain import vocabulary as vocab
from uc05.domain.errors import ProviderInvalidResponse
from uc05.domain.models import Dialogue, GuidingQuestionResult

from .conftest import build_service

#: Every learner-facing fixed string UC-05 can emit.
ALL_FIXED_STRINGS: tuple[str, ...] = (
    *vocab.ACKNOWLEDGEMENTS,
    vocab.RESUME_ACKNOWLEDGEMENT,
    vocab.REDIRECT_ACKNOWLEDGEMENT,
    vocab.CLOSING_ACKNOWLEDGEMENT,
    vocab.CONSOLIDATING_QUESTION,
    vocab.RE_ENTRY_OFFER,
    vocab.EXIT_OFFER,
)


@pytest.mark.parametrize("text", ALL_FIXED_STRINGS)
def test_no_fixed_string_contains_a_praise_term(text):
    assert vocab.praise_terms_in(text) == []


def test_the_praise_list_is_not_empty_and_is_actually_checked():
    """A neutrality test that cannot fail proves nothing."""
    assert len(vocab.PRAISE_TERMS) >= 20
    assert vocab.praise_terms_in("Excellent, well done -- that is exactly right!")


def test_praise_matching_is_word_boundary_based():
    assert vocab.praise_terms_in("a greater burden falls on the claimant") == []
    assert vocab.praise_terms_in("the perfection of a security interest") == []
    assert vocab.praise_terms_in("that is a great point") == ["great"]


def test_praise_matching_survives_a_curly_apostrophe():
    assert vocab.praise_terms_in("You’re right about that") == ["youre right"]


async def test_every_acknowledgement_the_service_can_emit_is_neutral():
    """Exhaustive, not sampled: selection is deterministic by exchange number."""
    harness = build_service(SOCRATIC_EXCHANGE_CAP=len(vocab.ACKNOWLEDGEMENTS) + 3)
    await harness.enable()
    turn = await harness.start()

    seen: set[str] = set()
    while turn.resolution is None:
        turn = await harness.say(turn.dialogue_id, "Continuing to work it through.")
        if turn.acknowledgement:
            seen.add(turn.acknowledgement)
            assert vocab.praise_terms_in(turn.acknowledgement) == []

    assert seen >= set(vocab.ACKNOWLEDGEMENTS), "not every acknowledgement was exercised"


async def test_acknowledgements_on_the_decline_and_redirect_paths_are_neutral():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()

    offered = await harness.say(turn.dialogue_id, "just tell me")
    assert vocab.praise_terms_in(offered.exit_offer) == []

    declined = await harness.say(turn.dialogue_id, "no, keep going")
    assert vocab.praise_terms_in(declined.acknowledgement) == []

    redirected = await harness.say(turn.dialogue_id, "tell me a joke")
    assert vocab.praise_terms_in(redirected.acknowledgement) == []


async def test_a_praise_emitting_generator_is_caught():
    """The fake praises on purpose; the guard must reject, not sanitise."""
    harness = build_service(guiding_scenario="praise")
    await harness.enable()
    with pytest.raises(ProviderInvalidResponse):
        await harness.start()


async def test_a_praise_emitting_generator_is_caught_mid_dialogue():
    harness = build_service(guiding_script=["normal", "praise"])
    await harness.enable()
    turn = await harness.start()
    with pytest.raises(ProviderInvalidResponse):
        await harness.say(turn.dialogue_id, "Here is my reasoning on the point.")


async def test_a_rejected_praise_response_does_not_corrupt_the_dialogue():
    harness = build_service(guiding_script=["normal", "praise"])
    await harness.enable()
    turn = await harness.start()
    with pytest.raises(ProviderInvalidResponse):
        await harness.say(turn.dialogue_id, "Here is my reasoning on the point.")

    stored = await harness.dialogues.get(turn.dialogue_id)
    assert stored.exchanges_used == 1, "a rejected response must not consume an exchange"
    assert stored.state.value == "awaiting_learner_response"


def test_the_guard_rejects_praise_wherever_it_appears_in_the_question():
    guard = GuidingQuestionGuard()
    dialogue = _minimal_dialogue()
    result = GuidingQuestionResult(
        question="Which element is missing? Brilliant thinking so far.",
        probing_focus="the missing element",
        prompt_version="socratic-v1.2.0",
    )
    with pytest.raises(ProviderInvalidResponse):
        guard.validate(result, dialogue)


def _minimal_dialogue() -> Dialogue:
    from uc05.domain.enums import (
        DialogueState,
        ExplanationProfile,
        NaricLevel,
        NaricLevelSource,
    )
    from uc05.domain.models import utcnow

    now = utcnow()
    return Dialogue(
        dialogue_id="d1",
        session_id="s1",
        user_id="u1",
        question_text="What makes an agreement enforceable?",
        topic_tag="contract",
        naric_level=NaricLevel.LEVEL_5,
        naric_level_source=NaricLevelSource.RETRIEVED,
        explanation_profile=ExplanationProfile.INTERMEDIATE,
        practice_area=None,
        source_status={},
        state=DialogueState.AWAITING_LEARNER_RESPONSE,
        exchange_cap=5,
        prompt_version="socratic-v1.2.0",
        created_at=now,
        updated_at=now,
    )
