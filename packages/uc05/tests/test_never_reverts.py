"""Section 5.8 -- Socratic mode never reverts silently.

The claim being tested is a negative one: there is no fifth path to a direct
answer.  A handful of positive examples cannot establish that, so this file
does two things a sampled test cannot:

*   it drives **every intent** against **every open state** and asserts that
    any turn carrying an answer also carries one of the four permitted
    resolutions;
*   it asserts over the transition table itself, so a future edit that adds a
    fifth answer path fails here even if no behavioural test exercises it.
"""

from __future__ import annotations

import itertools

import pytest

from uc05.domain import state_machine as sm
from uc05.domain.enums import DialogueState, IntentKind, Resolution, ResponseKind
from uc05.domain.errors import ProviderInvalidResponse

from .conftest import build_service

PERMITTED = {
    Resolution.EXITED_ON_REQUEST,
    Resolution.EXITED_ON_FRUSTRATION,
    Resolution.CAPPED,
    Resolution.LOOP_DETECTED,
}

OPEN_STATES = (
    DialogueState.AWAITING_LEARNER_RESPONSE,
    DialogueState.AWAITING_EXIT_CONFIRMATION,
)


async def _dialogue_in_state(state: DialogueState, **kwargs):
    """Build a live dialogue sitting in the requested state."""
    harness = build_service(**kwargs)
    await harness.enable()
    turn = await harness.start()
    if state is DialogueState.AWAITING_EXIT_CONFIRMATION:
        turn = await harness.say(turn.dialogue_id, "just tell me")
        assert turn.state is state
    return harness, turn


@pytest.mark.parametrize(
    "state,intent", list(itertools.product(OPEN_STATES, list(IntentKind)))
)
async def test_no_intent_in_any_state_yields_an_unauthorised_answer(state, intent):
    harness, turn = await _dialogue_in_state(state)

    # Force the intent rather than hunting for a phrase that produces it: the
    # point is to cover the whole grid, including combinations no realistic
    # wording would reach.
    harness.intents.force = intent
    result = await harness.say(turn.dialogue_id, "some learner message")

    if result.answer is not None:
        assert result.resolution in PERMITTED, (state, intent, result.resolution)
        assert result.response_kind in sm.ANSWER_BEARING_KINDS
    else:
        assert result.resolution not in PERMITTED or result.resolution is None


@pytest.mark.parametrize("state", OPEN_STATES)
async def test_every_answer_bearing_turn_names_its_transition(state):
    """A direct answer is always attributable to a named rule."""
    harness, turn = await _dialogue_in_state(state)
    harness.intents.force = IntentKind.EXPLICIT_FRUSTRATION
    result = await harness.say(turn.dialogue_id, "message")

    assert result.answer is not None
    assert result.transition
    transition = next(t for t in sm.TRANSITIONS if t.name == result.transition)
    assert transition.resolution in PERMITTED


async def test_all_four_permitted_paths_are_reachable_and_no_others():
    """Each of the four, produced end to end; the set is exactly four."""
    observed: dict[Resolution, str] = {}

    # 1. confirmed exit on request
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    await harness.say(turn.dialogue_id, "just tell me")
    result = await harness.say(turn.dialogue_id, "yes")
    observed[result.resolution] = result.transition

    # 2. frustration exit
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    result = await harness.say(turn.dialogue_id, "I genuinely have no idea.")
    observed[result.resolution] = result.transition

    # 3. natural cap
    harness = build_service()
    await harness.enable()
    turn = await harness.start()
    while turn.resolution is None:
        turn = await harness.say(turn.dialogue_id, "still working on it")
    observed[turn.resolution] = turn.transition

    # 4. loop detection
    harness = build_service(guiding_script=["normal", "verbatim_repeat"])
    await harness.enable()
    turn = await harness.start()
    result = await harness.say(turn.dialogue_id, "still working on it")
    observed[result.resolution] = result.transition

    assert set(observed) == PERMITTED


async def test_learner_reasoned_closes_the_dialogue_without_an_answer():
    """The fifth resolution exists, and deliberately is not an answer path."""
    harness = build_service()
    await harness.enable()
    turn = await harness.start()

    result = await harness.say(
        turn.dialogue_id,
        "So the answer is that consideration must move from the promisee.",
    )

    assert result.resolution is Resolution.LEARNER_REASONED
    assert result.answer is None
    assert result.state is DialogueState.RESOLVED
    assert result.guiding_question, "it closes with a consolidating question"
    # Integration brief §4.2: this path publishes the sixth response_kind, so a
    # reader of the interaction log can tell "the learner got there" apart from
    # "still working". Before the rename both published
    # acknowledgement_and_guiding_question and were indistinguishable.
    assert result.response_kind is ResponseKind.CLOSING_ACKNOWLEDGEMENT


async def test_a_generator_returning_a_direct_answer_is_rejected():
    harness = build_service(guiding_scenario="direct_answer")
    await harness.enable()
    with pytest.raises(ProviderInvalidResponse):
        await harness.start()


async def test_a_generator_returning_a_direct_answer_mid_dialogue_is_rejected():
    harness = build_service(guiding_script=["normal", "direct_answer"])
    await harness.enable()
    turn = await harness.start()
    with pytest.raises(ProviderInvalidResponse):
        await harness.say(turn.dialogue_id, "my reasoning so far")


async def test_a_rejected_direct_answer_never_reaches_the_learner():
    harness = build_service(guiding_script=["normal", "direct_answer"])
    await harness.enable()
    turn = await harness.start()
    with pytest.raises(ProviderInvalidResponse):
        await harness.say(turn.dialogue_id, "my reasoning so far")

    stored = await harness.dialogues.get(turn.dialogue_id)
    assert stored.exchanges_used == 1
    assert stored.resolution is None
    assert stored.state is DialogueState.AWAITING_LEARNER_RESPONSE
    records = await harness.records()
    assert all(r.response_kind is not ResponseKind.DIRECT_ANSWER for r in records)


def test_the_transition_table_admits_no_fifth_answer_path():
    answer_resolutions = {
        transition.resolution
        for transition in sm.TRANSITIONS
        if transition.response_kind in sm.ANSWER_BEARING_KINDS
    }
    assert answer_resolutions == PERMITTED
