"""Section 5.5 -- exit on frustration: one step, and only on explicit statements.

Both directions are tested, because both are the requirement.  Rescuing someone
who asked to be rescued is half the behaviour; *not* rescuing someone who is
enjoying a hard problem is the other half, and it is the half a sentiment score
would get wrong.
"""

from __future__ import annotations

import pytest

from uc05.domain import vocabulary as vocab
from uc05.domain.enums import DialogueState, IntentKind, Resolution, ResponseKind
from uc05.domain.intent_rules import classify_message

from .conftest import SESSION, USER, build_service

#: Explicit statements of being stuck.  These MUST trigger an immediate exit.
EXPLICIT_PHRASINGS = (
    "I genuinely have no idea.",
    "I'm completely lost.",
    "I give up, I don't know.",
    "I honestly don't know.",
    "I'm stuck.",
    "I have no clue",
)

#: Casual difficulty.  These MUST NOT trigger an exit.
CASUAL_PHRASINGS = (
    "ugh, this is hard",
    "lol I'm terrible at this",
    "this is doing my head in",
    "wow this one is tough",
    "my brain hurts",
    "this is really tricky",
)


# --------------------------------------------------------------------------
# Detection is explicit-statement based, not sentiment scoring
# --------------------------------------------------------------------------


@pytest.mark.parametrize("phrasing", EXPLICIT_PHRASINGS)
def test_explicit_phrasings_classify_as_frustration(phrasing):
    assert classify_message(phrasing).kind is IntentKind.EXPLICIT_FRUSTRATION


@pytest.mark.parametrize("phrasing", CASUAL_PHRASINGS)
def test_casual_phrasings_classify_as_casual_difficulty_not_frustration(phrasing):
    outcome = classify_message(phrasing)
    assert outcome.kind is IntentKind.CASUAL_DIFFICULTY
    assert outcome.kind is not IntentKind.EXPLICIT_FRUSTRATION


def test_frustration_and_casual_difficulty_are_separable_outputs():
    assert IntentKind.CASUAL_DIFFICULTY is not IntentKind.EXPLICIT_FRUSTRATION
    assert {classify_message(p).kind for p in EXPLICIT_PHRASINGS} == {
        IntentKind.EXPLICIT_FRUSTRATION
    }
    assert {classify_message(p).kind for p in CASUAL_PHRASINGS} == {
        IntentKind.CASUAL_DIFFICULTY
    }


def test_the_phrase_sets_are_disjoint():
    vocab.assert_disjoint_phrase_sets()


def test_detection_matches_a_whole_clause_not_a_substring():
    """"I don't know if X" is reasoning; "I don't know." is being stuck."""
    assert (
        classify_message("I don't know if consideration applies here").kind
        is IntentKind.SUBSTANTIVE_RESPONSE
    )
    assert classify_message("I don't know.").kind is IntentKind.EXPLICIT_FRUSTRATION


def test_a_stock_phrase_alongside_real_reasoning_does_not_rescue():
    """A-FRUSTRATION-RULE: still reasoning means still in the dialogue."""
    message = "I'm stuck, but is it because consideration must move from the promisee?"
    assert classify_message(message).kind is IntentKind.SUBSTANTIVE_RESPONSE


def test_the_matched_phrase_recorded_comes_from_our_own_vocabulary():
    outcome = classify_message("I genuinely have no idea.")
    assert outcome.matched_phrase in vocab.EXPLICIT_FRUSTRATION_PHRASES


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


@pytest.mark.parametrize("phrasing", EXPLICIT_PHRASINGS)
async def test_explicit_frustration_exits_immediately_with_an_explanation(phrasing):
    harness = build_service()
    await harness.enable()
    turn = await harness.start()

    exited = await harness.say(turn.dialogue_id, phrasing)

    assert exited.response_kind is ResponseKind.DIRECT_ANSWER, phrasing
    assert exited.resolution is Resolution.EXITED_ON_FRUSTRATION, phrasing
    assert exited.state is DialogueState.EXITED_FOR_QUESTION
    assert exited.answer is not None
    assert exited.answer.plain_english_explanation


@pytest.mark.parametrize("phrasing", EXPLICIT_PHRASINGS)
async def test_a_frustration_exit_offers_re_entry(phrasing):
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    exited = await harness.say(turn.dialogue_id, phrasing)

    assert exited.re_entry_offer == vocab.RE_ENTRY_OFFER
    assert vocab.praise_terms_in(exited.re_entry_offer) == []


@pytest.mark.parametrize("phrasing", EXPLICIT_PHRASINGS)
async def test_no_confirmation_step_is_required(phrasing):
    """Unlike 5.4, this is one step: the answer arrives on the first message."""
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    exited = await harness.say(turn.dialogue_id, phrasing)
    assert exited.state is not DialogueState.AWAITING_EXIT_CONFIRMATION
    assert exited.answer is not None


@pytest.mark.parametrize("phrasing", CASUAL_PHRASINGS)
async def test_casual_difficulty_does_not_trigger_an_exit(phrasing):
    harness = build_service()
    await harness.enable()
    turn = await harness.start()

    reply = await harness.say(turn.dialogue_id, phrasing)

    assert reply.answer is None, phrasing
    assert reply.resolution is None, phrasing
    assert reply.response_kind is ResponseKind.ACKNOWLEDGEMENT_AND_GUIDING_QUESTION
    assert reply.state is DialogueState.AWAITING_LEARNER_RESPONSE


@pytest.mark.parametrize("phrasing", CASUAL_PHRASINGS)
async def test_casual_difficulty_continues_the_dialogue_normally(phrasing):
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    reply = await harness.say(turn.dialogue_id, phrasing)
    assert reply.exchanges_used == 2
    assert reply.guiding_question


async def test_re_entry_applies_from_the_next_question():
    harness = build_service()
    await harness.enable()
    first = await harness.start()
    await harness.say(first.dialogue_id, "I genuinely have no idea.")

    mode = await harness.service.get_mode(SESSION, USER)
    assert mode.enabled is True, "the mode is not switched off by a frustration exit"

    second = await harness.start(question="What makes a variation binding?")
    assert second.response_kind is ResponseKind.GUIDING_QUESTION
    assert second.answer is None


async def test_frustration_takes_precedence_over_an_open_exit_offer():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "just tell me")

    exited = await harness.say(turn.dialogue_id, "I'm completely lost.")

    assert exited.resolution is Resolution.EXITED_ON_FRUSTRATION
    assert exited.resolution is not Resolution.EXITED_ON_REQUEST


async def test_a_frustration_exit_does_not_deliver_a_reasoning_chain():
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    exited = await harness.say(turn.dialogue_id, "I give up")
    assert exited.reasoning_chain is None
