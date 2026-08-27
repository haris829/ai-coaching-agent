"""Section 5.7 -- loop protection.

Raw string equality would catch a verbatim repeat and miss a reworded one, so
the comparison is on a normalised, semantically meaningful form.  These tests
cover both, and also pin the properties of the question bank that make the
"reworded" case a genuine test rather than a coincidence.
"""

from __future__ import annotations

import pytest

from uc05.adapters.fake.question_bank import BANK
from uc05.domain.enums import DialogueState, Resolution, ResponseKind
from uc05.domain.normalisation import (
    LOOP_SIMILARITY_THRESHOLD,
    fingerprint,
    is_repeat,
    similarity,
)

from .conftest import build_service

REPLY = "Here is another attempt at the reasoning."


# --------------------------------------------------------------------------
# The comparison itself -- deterministic, testable without a model
# --------------------------------------------------------------------------


def test_a_verbatim_repeat_has_an_identical_fingerprint():
    question = BANK[0].question
    assert fingerprint(question) == fingerprint(question)
    assert similarity(question, question) == 1.0


def test_distinct_bank_questions_are_far_below_the_threshold():
    """Otherwise a normal five-exchange dialogue would trip loop detection."""
    for i, left in enumerate(BANK):
        for right in BANK[i + 1 :]:
            assert similarity(left.question, right.question) < LOOP_SIMILARITY_THRESHOLD


def test_each_reworded_variant_is_a_genuine_rewording():
    for entry in BANK:
        assert entry.question != entry.reworded
        assert similarity(entry.question, entry.reworded) >= LOOP_SIMILARITY_THRESHOLD


def test_a_reworded_repeat_defeats_raw_string_equality():
    entry = BANK[0]
    assert entry.question != entry.reworded
    assert fingerprint(entry.question) != fingerprint(entry.reworded)
    repeat, index, score = is_repeat(entry.reworded, [entry.question])
    assert repeat is True
    assert index == 0
    assert score >= LOOP_SIMILARITY_THRESHOLD


def test_a_question_that_advances_is_not_a_repeat():
    repeat, _, score = is_repeat(BANK[1].question, [BANK[0].question])
    assert repeat is False
    assert score < LOOP_SIMILARITY_THRESHOLD


def test_is_repeat_reports_which_earlier_question_matched():
    previous = [entry.question for entry in BANK[:4]]
    repeat, index, _ = is_repeat(BANK[2].reworded, previous)
    assert repeat is True
    assert index == 2


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


async def test_a_verbatim_repeat_forces_the_cap_early():
    harness = build_service(guiding_script=["normal", "verbatim_repeat"])
    await harness.enable()
    turn = await harness.start()
    assert turn.exchanges_used == 1

    looped = await harness.say(turn.dialogue_id, REPLY)

    assert looped.resolution is Resolution.LOOP_DETECTED
    assert looped.response_kind is ResponseKind.CAPPED_ANSWER
    assert looped.state is DialogueState.CAPPED
    assert looped.exchanges_used == 1, "the cap was forced early, not reached"
    assert looped.exchanges_remaining == 4


async def test_a_reworded_repeat_is_also_caught():
    harness = build_service(guiding_script=["normal", "reworded_repeat"])
    await harness.enable()
    turn = await harness.start()

    looped = await harness.say(turn.dialogue_id, REPLY)

    assert looped.resolution is Resolution.LOOP_DETECTED
    assert looped.response_kind is ResponseKind.CAPPED_ANSWER


async def test_a_repeat_late_in_a_dialogue_is_caught():
    harness = build_service(
        guiding_script=["normal", "normal", "normal", "reworded_repeat"]
    )
    await harness.enable()
    turn = await harness.start()
    turn = await harness.say(turn.dialogue_id, REPLY)
    turn = await harness.say(turn.dialogue_id, REPLY)
    assert turn.exchanges_used == 3

    looped = await harness.say(turn.dialogue_id, REPLY)
    assert looped.resolution is Resolution.LOOP_DETECTED
    assert looped.exchanges_used == 3


async def test_loop_detected_is_recorded_distinctly_from_a_natural_cap():
    looped = build_service(guiding_script=["normal", "verbatim_repeat"])
    await looped.enable()
    turn = await looped.start()
    loop_turn = await looped.say(turn.dialogue_id, REPLY)

    natural = build_service()
    await natural.enable()
    turn = await natural.start()
    while turn.resolution is None:
        turn = await natural.say(turn.dialogue_id, REPLY)

    assert loop_turn.resolution is Resolution.LOOP_DETECTED
    assert turn.resolution is Resolution.CAPPED
    assert loop_turn.resolution is not turn.resolution

    # ... and the distinction survives into the interaction log.
    loop_records = await looped.records()
    natural_records = await natural.records()
    assert loop_records[-1].resolution is Resolution.LOOP_DETECTED
    assert natural_records[-1].resolution is Resolution.CAPPED


async def test_a_loop_delivers_the_answer_and_the_reasoning_chain():
    harness = build_service(guiding_script=["normal", "normal", "verbatim_repeat"])
    await harness.enable()
    turn = await harness.start()
    turn = await harness.say(turn.dialogue_id, REPLY)

    looped = await harness.say(turn.dialogue_id, REPLY)

    assert looped.answer is not None
    assert looped.reasoning_chain is not None
    assert len(looped.reasoning_chain) == 2, "only the questions actually asked"


async def test_the_repeated_question_is_never_shown_to_the_learner():
    harness = build_service(guiding_script=["normal", "verbatim_repeat"])
    await harness.enable()
    turn = await harness.start()
    first = turn.guiding_question

    looped = await harness.say(turn.dialogue_id, REPLY)

    assert looped.guiding_question is None
    stored = await harness.dialogues.get(turn.dialogue_id)
    assert [e.guiding_question for e in stored.exchanges] == [first]


async def test_the_matched_exchange_is_recorded_for_inspection():
    harness = build_service(guiding_script=["normal", "normal", "reworded_repeat"])
    await harness.enable()
    turn = await harness.start()
    turn = await harness.say(turn.dialogue_id, REPLY)
    looped = await harness.say(turn.dialogue_id, REPLY)

    stored = await harness.dialogues.get(looped.dialogue_id)
    assert stored.loop_matched_exchange == 2


@pytest.mark.parametrize("threshold", [0.5, 0.95])
async def test_the_threshold_is_configurable(threshold):
    harness = build_service(
        guiding_script=["normal", "reworded_repeat"],
        LOOP_SIMILARITY_THRESHOLD=threshold,
    )
    await harness.enable()
    turn = await harness.start()
    result = await harness.say(turn.dialogue_id, REPLY)

    if threshold <= 0.85:
        assert result.resolution is Resolution.LOOP_DETECTED
    else:
        assert result.resolution is None, "a stricter threshold lets it through"
