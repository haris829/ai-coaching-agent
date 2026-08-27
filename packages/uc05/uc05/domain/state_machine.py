"""The dialogue state machine -- the core of UC-05.

Every behavioural rule in section 5 of the brief is a transition rule, so they
all live here, in one declarative table, and can be verified by reading it
without running the service.

Reading the table
-----------------

``TRANSITIONS`` is a tuple of ``Transition`` rows.  A row says: from state
``source``, event ``event`` moves the dialogue to ``target``, produces
``response_kind``, sets ``resolution`` (``None`` leaves the dialogue open) and
either does or does not open a new exchange.

``opens_exchange`` is the exchange accounting.  An exchange is opened when the
system emits a *new* guiding question.  Re-posing the question the learner has
not yet answered -- which is what happens when an exit offer is declined or an
off-topic message is redirected -- does not open one, which is how the rule
"declining an exit leaves the exchange count unaffected" is enforced
structurally rather than by remembering to skip an increment.

Intents are not events
----------------------

``event_for_intent`` maps a classified intent onto an event *given the current
state*.  This is where "never exit unilaterally" is enforced: a bare "yes"
arriving in ``AWAITING_LEARNER_RESPONSE`` is a substantive response, because
no exit was offered; the same "yes" in ``AWAITING_EXIT_CONFIRMATION`` is a
confirmation.  There is no path from a single learner message to a direct
answer.

Cap and loop
------------

``CAP_REACHED`` and ``LOOP_DETECTED`` are events the *application* raises after
consulting the persisted dialogue (cap) and the freshly generated question
(loop).  Keeping them as events rather than as conditions inside the table
keeps the table pure and every transition individually testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import (
    TERMINAL_STATES,
    DialogueEvent,
    DialogueState,
    IntentKind,
    Resolution,
    ResponseKind,
)
from .errors import InvalidTransition

INITIAL_STATE = DialogueState.AWAITING_LEARNER_RESPONSE


@dataclass(frozen=True)
class Transition:
    name: str
    source: DialogueState | None  # None == dialogue creation
    event: DialogueEvent
    target: DialogueState
    response_kind: ResponseKind | None
    resolution: Resolution | None
    opens_exchange: bool
    #: True when the response re-poses the question already on the table
    #: instead of generating a new one.
    reposes_current_question: bool = False
    note: str = ""


AWAITING = DialogueState.AWAITING_LEARNER_RESPONSE
CONFIRMING = DialogueState.AWAITING_EXIT_CONFIRMATION

TRANSITIONS: tuple[Transition, ...] = (
    # -- creation ---------------------------------------------------------
    Transition(
        name="T01_start",
        source=None,
        event=DialogueEvent.DIALOGUE_STARTED,
        target=AWAITING,
        response_kind=ResponseKind.GUIDING_QUESTION,
        resolution=None,
        opens_exchange=True,
        note="5.2 first reply is a guiding question, never an answer",
    ),
    # -- ordinary progress -------------------------------------------------
    Transition(
        name="T02_continue",
        source=AWAITING,
        event=DialogueEvent.SUBSTANTIVE_RESPONSE,
        target=AWAITING,
        response_kind=ResponseKind.ACKNOWLEDGEMENT_AND_GUIDING_QUESTION,
        resolution=None,
        opens_exchange=True,
        note="5.2 from the second exchange onward, acknowledgement + question",
    ),
    Transition(
        name="T03_redirect_off_topic",
        source=AWAITING,
        event=DialogueEvent.OFF_TOPIC,
        target=AWAITING,
        response_kind=ResponseKind.ACKNOWLEDGEMENT_AND_GUIDING_QUESTION,
        resolution=None,
        opens_exchange=False,
        reposes_current_question=True,
        note="A-OFFTOPIC-NO-COST: an off-topic message is not learner reasoning",
    ),
    Transition(
        name="T04_learner_reasoned",
        source=AWAITING,
        event=DialogueEvent.LEARNER_REASONED_CONCLUSION,
        target=DialogueState.RESOLVED,
        response_kind=ResponseKind.CLOSING_ACKNOWLEDGEMENT,
        resolution=Resolution.LEARNER_REASONED,
        opens_exchange=False,
        reposes_current_question=False,
        note="A-CLOSURE-KIND: closes with a consolidating question, not an answer",
    ),
    # -- exit on request: two steps, never one ----------------------------
    Transition(
        name="T05_offer_exit",
        source=AWAITING,
        event=DialogueEvent.DIRECT_ANSWER_REQUESTED,
        target=CONFIRMING,
        response_kind=ResponseKind.EXIT_OFFER,
        resolution=None,
        opens_exchange=False,
        note="5.4 step 1: acknowledge the request and OFFER to exit",
    ),
    Transition(
        name="T06_exit_confirmed",
        source=CONFIRMING,
        event=DialogueEvent.EXIT_CONFIRMED,
        target=DialogueState.EXITED_FOR_QUESTION,
        response_kind=ResponseKind.DIRECT_ANSWER,
        resolution=Resolution.EXITED_ON_REQUEST,
        opens_exchange=False,
        note="5.4 step 2: only on confirmation does a direct answer appear",
    ),
    Transition(
        name="T07_exit_declined",
        source=CONFIRMING,
        event=DialogueEvent.EXIT_DECLINED,
        target=AWAITING,
        response_kind=ResponseKind.ACKNOWLEDGEMENT_AND_GUIDING_QUESTION,
        resolution=None,
        opens_exchange=False,
        reposes_current_question=True,
        note="5.4 dialogue continues from where it was; count unaffected",
    ),
    Transition(
        name="T08_exit_reasserted",
        source=CONFIRMING,
        event=DialogueEvent.DIRECT_ANSWER_REQUESTED,
        target=DialogueState.EXITED_FOR_QUESTION,
        response_kind=ResponseKind.DIRECT_ANSWER,
        resolution=Resolution.EXITED_ON_REQUEST,
        opens_exchange=False,
        note="A-REASSERT: repeating the request after an offer confirms it",
    ),
    Transition(
        name="T09_implicit_decline",
        source=CONFIRMING,
        event=DialogueEvent.SUBSTANTIVE_RESPONSE,
        target=AWAITING,
        response_kind=ResponseKind.ACKNOWLEDGEMENT_AND_GUIDING_QUESTION,
        resolution=None,
        opens_exchange=True,
        note="A-IMPLICIT-DECLINE: reasoning instead of answering the offer",
    ),
    Transition(
        name="T10_reoffer_exit",
        source=CONFIRMING,
        event=DialogueEvent.OFF_TOPIC,
        target=CONFIRMING,
        response_kind=ResponseKind.EXIT_OFFER,
        resolution=None,
        opens_exchange=False,
        note="the offer stands until answered",
    ),
    Transition(
        name="T11_conclusion_while_confirming",
        source=CONFIRMING,
        event=DialogueEvent.LEARNER_REASONED_CONCLUSION,
        target=DialogueState.RESOLVED,
        response_kind=ResponseKind.CLOSING_ACKNOWLEDGEMENT,
        resolution=Resolution.LEARNER_REASONED,
        opens_exchange=False,
        note="the learner got there while the offer was open",
    ),
    # -- exit on frustration: one step, no confirmation --------------------
    Transition(
        name="T12_frustration_exit",
        source=AWAITING,
        event=DialogueEvent.EXPLICIT_FRUSTRATION,
        target=DialogueState.EXITED_FOR_QUESTION,
        response_kind=ResponseKind.DIRECT_ANSWER,
        resolution=Resolution.EXITED_ON_FRUSTRATION,
        opens_exchange=False,
        note="5.5 immediate exit, direct explanation, re-entry offer",
    ),
    Transition(
        name="T13_frustration_while_confirming",
        source=CONFIRMING,
        event=DialogueEvent.EXPLICIT_FRUSTRATION,
        target=DialogueState.EXITED_FOR_QUESTION,
        response_kind=ResponseKind.DIRECT_ANSWER,
        resolution=Resolution.EXITED_ON_FRUSTRATION,
        opens_exchange=False,
        note="5.5 takes precedence over an open offer",
    ),
    # -- cap and loop ------------------------------------------------------
    Transition(
        name="T14_cap",
        source=AWAITING,
        event=DialogueEvent.CAP_REACHED,
        target=DialogueState.CAPPED,
        response_kind=ResponseKind.CAPPED_ANSWER,
        resolution=Resolution.CAPPED,
        opens_exchange=False,
        note="5.6 answer plus the reasoning chain, assembled from the record",
    ),
    Transition(
        name="T15_cap_while_confirming",
        source=CONFIRMING,
        event=DialogueEvent.CAP_REACHED,
        target=DialogueState.CAPPED,
        response_kind=ResponseKind.CAPPED_ANSWER,
        resolution=Resolution.CAPPED,
        opens_exchange=False,
        note="the cap binds regardless of an open offer",
    ),
    Transition(
        name="T16_loop",
        source=AWAITING,
        event=DialogueEvent.LOOP_DETECTED,
        target=DialogueState.CAPPED,
        response_kind=ResponseKind.CAPPED_ANSWER,
        resolution=Resolution.LOOP_DETECTED,
        opens_exchange=False,
        note="5.7 force the cap early; recorded distinctly from a natural cap",
    ),
    Transition(
        name="T17_loop_while_confirming",
        source=CONFIRMING,
        event=DialogueEvent.LOOP_DETECTED,
        target=DialogueState.CAPPED,
        response_kind=ResponseKind.CAPPED_ANSWER,
        resolution=Resolution.LOOP_DETECTED,
        opens_exchange=False,
        note="5.7 applies to the question generated after an implicit decline",
    ),
    # -- mode toggled off --------------------------------------------------
    Transition(
        name="T18_abandon_awaiting",
        source=AWAITING,
        event=DialogueEvent.MODE_TOGGLED_OFF,
        target=DialogueState.ABANDONED,
        response_kind=None,
        resolution=Resolution.ABANDONED,
        opens_exchange=False,
        note="5.1 an in-flight dialogue is closed and recorded, not dropped",
    ),
    Transition(
        name="T19_abandon_confirming",
        source=CONFIRMING,
        event=DialogueEvent.MODE_TOGGLED_OFF,
        target=DialogueState.ABANDONED,
        response_kind=None,
        resolution=Resolution.ABANDONED,
        opens_exchange=False,
        note="5.1 likewise with an offer outstanding",
    ),
)

_TRANSITION_INDEX: dict[tuple[DialogueState | None, DialogueEvent], Transition] = {
    (transition.source, transition.event): transition for transition in TRANSITIONS
}

# Guard against two rows claiming the same (state, event) pair.
assert len(_TRANSITION_INDEX) == len(TRANSITIONS), "duplicate (state, event) in TRANSITIONS"


def lookup(state: DialogueState | None, event: DialogueEvent) -> Transition:
    """Resolve a transition, or refuse.

    Terminal states have no outbound rows at all, so any event arriving at a
    closed dialogue raises ``InvalidTransition``.  There is no "reopen".
    """
    try:
        return _TRANSITION_INDEX[(state, event)]
    except KeyError:
        raise InvalidTransition(
            state.value if state is not None else "<new>", event.value
        ) from None


def is_legal(state: DialogueState | None, event: DialogueEvent) -> bool:
    return (state, event) in _TRANSITION_INDEX


def outbound(state: DialogueState | None) -> tuple[Transition, ...]:
    return tuple(t for t in TRANSITIONS if t.source == state)


# --------------------------------------------------------------------------
# Intent -> event, conditioned on state
# --------------------------------------------------------------------------

#: In ``AWAITING_LEARNER_RESPONSE`` nobody has been offered an exit, so a
#: confirmation or a decline is just a learner talking: it is treated as a
#: substantive response.  This is the structural guarantee behind "never exit
#: unilaterally on the first request".
_INTENT_EVENT_BY_STATE: dict[
    DialogueState, dict[IntentKind, DialogueEvent]
] = {
    AWAITING: {
        IntentKind.SUBSTANTIVE_RESPONSE: DialogueEvent.SUBSTANTIVE_RESPONSE,
        IntentKind.CASUAL_DIFFICULTY: DialogueEvent.SUBSTANTIVE_RESPONSE,
        IntentKind.EXIT_CONFIRMATION: DialogueEvent.SUBSTANTIVE_RESPONSE,
        IntentKind.EXIT_DECLINED: DialogueEvent.SUBSTANTIVE_RESPONSE,
        IntentKind.LEARNER_REASONED_CONCLUSION: DialogueEvent.LEARNER_REASONED_CONCLUSION,
        IntentKind.DIRECT_ANSWER_REQUEST: DialogueEvent.DIRECT_ANSWER_REQUESTED,
        IntentKind.EXPLICIT_FRUSTRATION: DialogueEvent.EXPLICIT_FRUSTRATION,
        IntentKind.OFF_TOPIC: DialogueEvent.OFF_TOPIC,
    },
    CONFIRMING: {
        IntentKind.SUBSTANTIVE_RESPONSE: DialogueEvent.SUBSTANTIVE_RESPONSE,
        IntentKind.CASUAL_DIFFICULTY: DialogueEvent.SUBSTANTIVE_RESPONSE,
        IntentKind.EXIT_CONFIRMATION: DialogueEvent.EXIT_CONFIRMED,
        IntentKind.EXIT_DECLINED: DialogueEvent.EXIT_DECLINED,
        IntentKind.LEARNER_REASONED_CONCLUSION: DialogueEvent.LEARNER_REASONED_CONCLUSION,
        IntentKind.DIRECT_ANSWER_REQUEST: DialogueEvent.DIRECT_ANSWER_REQUESTED,
        IntentKind.EXPLICIT_FRUSTRATION: DialogueEvent.EXPLICIT_FRUSTRATION,
        IntentKind.OFF_TOPIC: DialogueEvent.OFF_TOPIC,
    },
}


def event_for_intent(state: DialogueState, intent: IntentKind) -> DialogueEvent:
    """Map a classified intent onto an event, given the dialogue's state."""
    if state in TERMINAL_STATES:
        raise InvalidTransition(state.value, intent.value)
    try:
        return _INTENT_EVENT_BY_STATE[state][intent]
    except KeyError:  # pragma: no cover - exhaustiveness is asserted in tests
        raise InvalidTransition(state.value, intent.value) from None


#: Events after which the application must generate a *new* guiding question
#: (and therefore run loop detection).  Everything else either re-poses the
#: current question or ends the dialogue.
GENERATING_EVENTS: frozenset[DialogueEvent] = frozenset(
    {DialogueEvent.DIALOGUE_STARTED, DialogueEvent.SUBSTANTIVE_RESPONSE}
)

#: The complete set of resolutions that may accompany a direct answer.  The
#: "never revert silently" rule is asserted against exactly this set.
DIRECT_ANSWER_RESOLUTIONS: frozenset[Resolution] = frozenset(
    {
        Resolution.EXITED_ON_REQUEST,
        Resolution.EXITED_ON_FRUSTRATION,
        Resolution.CAPPED,
        Resolution.LOOP_DETECTED,
    }
)

#: Response kinds that carry a four-part answer payload.
ANSWER_BEARING_KINDS: frozenset[ResponseKind] = frozenset(
    {ResponseKind.DIRECT_ANSWER, ResponseKind.CAPPED_ANSWER}
)
