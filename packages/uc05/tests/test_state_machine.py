"""The state machine, tested in isolation -- no generator, no service, no HTTP.

The brief asks that a reviewer be able to verify the rules in section 5 by
reading the machine.  These tests are the executable half of that claim: they
assert the *table*, not the behaviour of the code that consults it.
"""

from __future__ import annotations

import pytest

from uc05.domain import state_machine as sm
from uc05.domain.enums import (
    TERMINAL_STATES,
    DialogueEvent,
    DialogueState,
    IntentKind,
    Resolution,
    ResponseKind,
)
from uc05.domain.errors import InvalidTransition

AWAITING = DialogueState.AWAITING_LEARNER_RESPONSE
CONFIRMING = DialogueState.AWAITING_EXIT_CONFIRMATION


def test_every_transition_has_a_unique_source_event_pair():
    pairs = [(t.source, t.event) for t in sm.TRANSITIONS]
    assert len(pairs) == len(set(pairs))


def test_every_transition_is_named_and_annotated():
    for transition in sm.TRANSITIONS:
        assert transition.name, transition
        assert transition.note, f"{transition.name} has no rule reference"


def test_terminal_states_have_no_outbound_transitions():
    for state in TERMINAL_STATES:
        assert sm.outbound(state) == ()


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES, key=lambda s: s.value))
@pytest.mark.parametrize("event", list(DialogueEvent))
def test_terminal_states_refuse_every_event(state, event):
    with pytest.raises(InvalidTransition):
        sm.lookup(state, event)


def test_only_creation_targets_the_initial_state_from_nothing():
    creators = [t for t in sm.TRANSITIONS if t.source is None]
    assert len(creators) == 1
    assert creators[0].event is DialogueEvent.DIALOGUE_STARTED
    assert creators[0].target is sm.INITIAL_STATE
    assert creators[0].response_kind is ResponseKind.GUIDING_QUESTION


# --------------------------------------------------------------------------
# Section 5.8 -- never revert silently.  This is the exhaustive assertion.
# --------------------------------------------------------------------------


def test_answer_bearing_transitions_carry_only_permitted_resolutions():
    answer_rows = [
        t for t in sm.TRANSITIONS if t.response_kind in sm.ANSWER_BEARING_KINDS
    ]
    assert answer_rows, "the table must contain at least one answer path"
    for transition in answer_rows:
        assert transition.resolution in sm.DIRECT_ANSWER_RESOLUTIONS, transition.name


def test_exactly_four_resolutions_can_accompany_a_direct_answer():
    assert {
        Resolution.EXITED_ON_REQUEST,
        Resolution.EXITED_ON_FRUSTRATION,
        Resolution.CAPPED,
        Resolution.LOOP_DETECTED,
    } == sm.DIRECT_ANSWER_RESOLUTIONS


def test_no_transition_produces_an_answer_without_a_resolution():
    for transition in sm.TRANSITIONS:
        if transition.resolution is None:
            assert transition.response_kind not in sm.ANSWER_BEARING_KINDS


def test_learner_reasoned_never_produces_an_answer():
    """The fifth resolution exists, and it is deliberately not an answer path."""
    rows = [t for t in sm.TRANSITIONS if t.resolution is Resolution.LEARNER_REASONED]
    assert rows
    for transition in rows:
        assert transition.response_kind not in sm.ANSWER_BEARING_KINDS


# --------------------------------------------------------------------------
# Section 5.4 -- exit on request is two steps, never one
# --------------------------------------------------------------------------


def test_a_direct_answer_request_from_awaiting_only_offers():
    transition = sm.lookup(AWAITING, DialogueEvent.DIRECT_ANSWER_REQUESTED)
    assert transition.target is CONFIRMING
    assert transition.response_kind is ResponseKind.EXIT_OFFER
    assert transition.resolution is None


def test_no_route_from_awaiting_to_exited_on_request_in_one_step():
    for transition in sm.outbound(AWAITING):
        if transition.resolution is Resolution.EXITED_ON_REQUEST:
            pytest.fail(f"{transition.name} exits on request without confirmation")


def test_exit_confirmation_is_only_reachable_from_confirming():
    rows = [t for t in sm.TRANSITIONS if t.event is DialogueEvent.EXIT_CONFIRMED]
    assert rows
    assert all(t.source is CONFIRMING for t in rows)


def test_declining_returns_to_awaiting_without_opening_an_exchange():
    transition = sm.lookup(CONFIRMING, DialogueEvent.EXIT_DECLINED)
    assert transition.target is AWAITING
    assert transition.opens_exchange is False
    assert transition.reposes_current_question is True
    assert transition.resolution is None


def test_the_exit_offer_itself_does_not_open_an_exchange():
    transition = sm.lookup(AWAITING, DialogueEvent.DIRECT_ANSWER_REQUESTED)
    assert transition.opens_exchange is False


# --------------------------------------------------------------------------
# Section 5.5 -- frustration is one step, no confirmation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("state", [AWAITING, CONFIRMING])
def test_explicit_frustration_exits_immediately_from_any_open_state(state):
    transition = sm.lookup(state, DialogueEvent.EXPLICIT_FRUSTRATION)
    assert transition.target is DialogueState.EXITED_FOR_QUESTION
    assert transition.resolution is Resolution.EXITED_ON_FRUSTRATION
    assert transition.response_kind is ResponseKind.DIRECT_ANSWER


# --------------------------------------------------------------------------
# Sections 5.6 / 5.7 -- cap and loop are distinct
# --------------------------------------------------------------------------


def test_cap_and_loop_share_a_target_but_not_a_resolution():
    cap = sm.lookup(AWAITING, DialogueEvent.CAP_REACHED)
    loop = sm.lookup(AWAITING, DialogueEvent.LOOP_DETECTED)
    assert cap.target is loop.target is DialogueState.CAPPED
    assert cap.resolution is Resolution.CAPPED
    assert loop.resolution is Resolution.LOOP_DETECTED
    assert cap.resolution is not loop.resolution


def test_neither_cap_nor_loop_opens_a_further_exchange():
    for event in (DialogueEvent.CAP_REACHED, DialogueEvent.LOOP_DETECTED):
        assert sm.lookup(AWAITING, event).opens_exchange is False


# --------------------------------------------------------------------------
# Section 5.1 -- toggling off closes, it does not drop
# --------------------------------------------------------------------------


@pytest.mark.parametrize("state", [AWAITING, CONFIRMING])
def test_mode_toggled_off_records_abandonment(state):
    transition = sm.lookup(state, DialogueEvent.MODE_TOGGLED_OFF)
    assert transition.target is DialogueState.ABANDONED
    assert transition.resolution is Resolution.ABANDONED
    assert transition.response_kind is None


# --------------------------------------------------------------------------
# Intent -> event mapping is state-conditioned
# --------------------------------------------------------------------------


@pytest.mark.parametrize("intent", list(IntentKind))
@pytest.mark.parametrize("state", [AWAITING, CONFIRMING])
def test_every_intent_maps_to_an_event_in_every_open_state(state, intent):
    event = sm.event_for_intent(state, intent)
    assert sm.is_legal(state, event), f"{state.value}/{intent.value} -> {event.value}"


@pytest.mark.parametrize(
    "intent", [IntentKind.EXIT_CONFIRMATION, IntentKind.EXIT_DECLINED]
)
def test_a_bare_yes_or_no_cannot_exit_when_no_offer_is_open(intent):
    """Without an offer, "yes" is a learner talking, not a confirmation."""
    event = sm.event_for_intent(AWAITING, intent)
    assert event is DialogueEvent.SUBSTANTIVE_RESPONSE
    assert sm.lookup(AWAITING, event).response_kind not in sm.ANSWER_BEARING_KINDS


def test_the_same_yes_confirms_once_an_offer_is_open():
    event = sm.event_for_intent(CONFIRMING, IntentKind.EXIT_CONFIRMATION)
    assert event is DialogueEvent.EXIT_CONFIRMED


def test_casual_difficulty_is_treated_as_a_substantive_response():
    for state in (AWAITING, CONFIRMING):
        assert (
            sm.event_for_intent(state, IntentKind.CASUAL_DIFFICULTY)
            is DialogueEvent.SUBSTANTIVE_RESPONSE
        )


def test_frustration_and_casual_difficulty_map_to_different_events():
    assert sm.event_for_intent(
        AWAITING, IntentKind.EXPLICIT_FRUSTRATION
    ) is not sm.event_for_intent(AWAITING, IntentKind.CASUAL_DIFFICULTY)


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES, key=lambda s: s.value))
def test_terminal_states_refuse_intent_mapping(state):
    with pytest.raises(InvalidTransition):
        sm.event_for_intent(state, IntentKind.SUBSTANTIVE_RESPONSE)


def test_only_generating_events_produce_a_new_question():
    assert {
        DialogueEvent.DIALOGUE_STARTED,
        DialogueEvent.SUBSTANTIVE_RESPONSE,
    } == sm.GENERATING_EVENTS
    for transition in sm.TRANSITIONS:
        if transition.opens_exchange:
            assert transition.event in sm.GENERATING_EVENTS, transition.name
