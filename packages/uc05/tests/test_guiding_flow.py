"""Sections 5.2 and 5.3 -- guiding questions, not answers."""

from __future__ import annotations

from itertools import pairwise

import pytest

from uc05.domain.enums import ResponseKind
from uc05.domain.errors import ProviderInvalidResponse
from uc05.domain.normalisation import similarity
from uc05.domain.vocabulary import praise_terms_in

from .conftest import QUESTION, build_service


async def test_the_first_reply_is_a_guiding_question_not_an_answer(harness):
    await harness.enable()
    turn = await harness.start()

    assert turn.response_kind is ResponseKind.GUIDING_QUESTION
    assert turn.answer is None
    assert turn.acknowledgement is None, "nothing to acknowledge yet"
    assert turn.guiding_question.endswith("?")


async def test_the_second_and_later_replies_pair_acknowledgement_with_a_question(harness):
    await harness.enable()
    turn = await harness.start()

    for _ in range(3):
        turn = await harness.say(
            turn.dialogue_id, "I think it turns on the second element."
        )
        assert turn.response_kind is ResponseKind.ACKNOWLEDGEMENT_AND_GUIDING_QUESTION
        assert turn.acknowledgement, "an acknowledgement is required from exchange two"
        assert turn.guiding_question
        assert turn.answer is None


async def test_the_guiding_question_advances_rather_than_restating(harness):
    await harness.enable()
    turn = await harness.start()
    asked = [turn.guiding_question]
    for _ in range(3):
        turn = await harness.say(
            turn.dialogue_id, "My reasoning so far is about formation."
        )
        asked.append(turn.guiding_question)

    # None of them merely restates the learner's own question ...
    for question in asked:
        assert similarity(question, QUESTION) < 0.85, question
    # ... and each moves on from the last.
    for earlier, later in pairwise(asked):
        assert similarity(earlier, later) < 0.8, (earlier, later)


async def test_exchange_counters_are_exposed_throughout(harness):
    await harness.enable()
    turn = await harness.start()
    assert (turn.exchanges_used, turn.exchanges_remaining) == (1, 4)

    for expected_used in (2, 3, 4, 5):
        turn = await harness.say(turn.dialogue_id, "Still working through the elements.")
        assert turn.exchanges_used == expected_used
        expected_remaining = harness.settings.socratic_exchange_cap - expected_used
        assert turn.exchanges_remaining == expected_remaining


async def test_the_generator_receives_persisted_state_not_its_own_memory(harness):
    """Dialogue state is never delegated to a generator's memory."""
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "First attempt at the reasoning.")

    stored = await harness.dialogues.get(turn.dialogue_id)
    assert [e.exchange_number for e in stored.exchanges] == [1, 2]
    assert stored.exchanges[0].learner_messages[0].text == "First attempt at the reasoning."
    assert stored.exchanges[0].question_fingerprint
    assert stored.state.value == "awaiting_learner_response"


async def test_a_generator_that_restates_the_question_is_rejected():
    harness = build_service(guiding_scenario="restating")
    await harness.enable()
    with pytest.raises(ProviderInvalidResponse):
        await harness.start()


async def test_a_malformed_generator_response_is_rejected():
    harness = build_service(guiding_scenario="malformed")
    await harness.enable()
    with pytest.raises(ProviderInvalidResponse):
        await harness.start()


async def test_no_guiding_question_ever_carries_praise(harness):
    await harness.enable()
    turn = await harness.start()
    emitted = [turn.guiding_question]
    for _ in range(4):
        turn = await harness.say(turn.dialogue_id, "Continuing the reasoning.")
        emitted.extend(filter(None, [turn.acknowledgement, turn.guiding_question]))

    for text in emitted:
        assert praise_terms_in(text) == [], text


async def test_context_reaches_the_dialogue_record():
    harness = build_service(context_scenario="level_7")
    await harness.enable()
    turn = await harness.start()

    assert turn.context.naric_level.value == "LEVEL_7"
    assert turn.context.explanation_profile.value == "advanced"
    stored = await harness.dialogues.get(turn.dialogue_id)
    assert stored.naric_level.value == "LEVEL_7"
    assert stored.explanation_profile.value == "advanced"


async def test_level_6_is_intermediate_not_advanced():
    """A-PROFILE-4-6: Level 6 is an undergraduate degree, not Masters level."""
    harness = build_service(context_scenario="level_6")
    await harness.enable()
    turn = await harness.start()
    assert turn.context.explanation_profile.value == "intermediate"


async def test_level_4_is_basic():
    harness = build_service(context_scenario="level_4")
    await harness.enable()
    turn = await harness.start()
    assert turn.context.explanation_profile.value == "basic"


async def test_off_topic_is_redirected_without_consuming_an_exchange(harness):
    await harness.enable()
    turn = await harness.start()
    posed = turn.guiding_question

    redirected = await harness.say(turn.dialogue_id, "what time is it")
    assert redirected.exchanges_used == 1, "an aside is not learner reasoning"
    assert redirected.guiding_question == posed, "the question still stands"
    assert redirected.answer is None
