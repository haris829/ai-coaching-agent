"""Closed vocabularies for UC-05.

Every enum in this module is either fixed by the platform contract (marked
SPECIFIED) or assumed by us and recorded in ``docs/assumptions.md`` (marked
ASSUMED).  Nothing here may be widened without a contract conversation.
"""

from __future__ import annotations

from enum import Enum


class NaricLevel(str, Enum):
    """SPECIFIED by the company.  Closed set; never an integer scale."""

    LEVEL_3 = "LEVEL_3"
    LEVEL_4 = "LEVEL_4"
    LEVEL_5 = "LEVEL_5"
    LEVEL_6 = "LEVEL_6"
    LEVEL_7 = "LEVEL_7"
    LEVEL_7_PLUS = "LEVEL_7_PLUS"


class NaricLevelSource(str, Enum):
    """SPECIFIED by the company."""

    RETRIEVED = "retrieved"
    DEFAULT = "default"


class ExplanationProfile(str, Enum):
    """SPECIFIED by the company."""

    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class SourceStatus(str, Enum):
    """SPECIFIED by the company.

    ``EMPTY`` (the source answered, and had nothing) and ``UNAVAILABLE``
    (the source did not answer) are different states and are never conflated.
    """

    AVAILABLE = "available"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class ResponseKind(str, Enum):
    """SPECIFIED by the company.  Published in the interaction log record."""

    GUIDING_QUESTION = "guiding_question"
    ACKNOWLEDGEMENT_AND_GUIDING_QUESTION = "acknowledgement_and_guiding_question"
    EXIT_OFFER = "exit_offer"
    DIRECT_ANSWER = "direct_answer"
    CAPPED_ANSWER = "capped_answer"
    #: The sixth member, added on the instruction in integration brief §4.2: the
    #: learner reasoned their way to the answer, so the dialogue closes with an
    #: acknowledgement rather than another attempt to elicit one.
    #:
    #: This relabels what UC-05 already did. Transitions T04 and T11 published
    #: ``acknowledgement_and_guiding_question`` while sending
    #: ``vocab.CLOSING_ACKNOWLEDGEMENT`` plus ``vocab.CONSOLIDATING_QUESTION``,
    #: so a reader could not tell "the learner got there" from "still working".
    #: The text emitted is unchanged; only the published label is.
    CLOSING_ACKNOWLEDGEMENT = "closing_acknowledgement"


class Resolution(str, Enum):
    """SPECIFIED by the company.  ``None`` means the dialogue is still open."""

    LEARNER_REASONED = "learner_reasoned"
    CAPPED = "capped"
    EXITED_ON_REQUEST = "exited_on_request"
    EXITED_ON_FRUSTRATION = "exited_on_frustration"
    LOOP_DETECTED = "loop_detected"
    ABANDONED = "abandoned"


class RatingState(str, Enum):
    """SPECIFIED by the company.  UC-05 only ever writes ``PENDING``."""

    PENDING = "pending"
    RATED = "rated"


class DialogueState(str, Enum):
    """SPECIFIED by the company (section 4 of the brief).

    ``AWAITING_LEARNER_RESPONSE`` is the only state a dialogue is created into;
    the four terminal states accept no further events.
    """

    AWAITING_LEARNER_RESPONSE = "awaiting_learner_response"
    AWAITING_EXIT_CONFIRMATION = "awaiting_exit_confirmation"
    RESOLVED = "resolved"
    CAPPED = "capped"
    EXITED_FOR_QUESTION = "exited_for_question"
    ABANDONED = "abandoned"


TERMINAL_STATES: frozenset[DialogueState] = frozenset(
    {
        DialogueState.RESOLVED,
        DialogueState.CAPPED,
        DialogueState.EXITED_FOR_QUESTION,
        DialogueState.ABANDONED,
    }
)


class IntentKind(str, Enum):
    """ASSUMED by us (A-INTENT-VOCAB).

    The brief fixes the six members marked SPECIFIED-MINIMUM below as the
    minimum the classifier must distinguish.  ``CASUAL_DIFFICULTY`` and
    ``LEARNER_REASONED_CONCLUSION`` are additions we needed: the first because
    casual difficulty must be *separable* from explicit frustration rather than
    silently folded into it, the second because the platform's
    ``learner_reasoned`` resolution otherwise has no reachable path.
    """

    SUBSTANTIVE_RESPONSE = "substantive_response"          # SPECIFIED-MINIMUM
    DIRECT_ANSWER_REQUEST = "direct_answer_request"        # SPECIFIED-MINIMUM
    EXIT_CONFIRMATION = "exit_confirmation"                # SPECIFIED-MINIMUM
    EXIT_DECLINED = "exit_declined"                        # SPECIFIED-MINIMUM
    EXPLICIT_FRUSTRATION = "explicit_frustration"          # SPECIFIED-MINIMUM
    OFF_TOPIC = "off_topic"                                # SPECIFIED-MINIMUM
    CASUAL_DIFFICULTY = "casual_difficulty"                # ASSUMED
    LEARNER_REASONED_CONCLUSION = "learner_reasoned_conclusion"  # ASSUMED


class DialogueEvent(str, Enum):
    """ASSUMED by us (A-SM-EVENTS).

    The brief requires that "every transition is triggered by an identified
    event".  These are those identifiers.  Intents are *mapped* to events in a
    state-conditioned way (see ``state_machine.event_for_intent``) so that, for
    example, a bare "yes" arriving when no exit was offered cannot exit.
    """

    DIALOGUE_STARTED = "dialogue_started"
    SUBSTANTIVE_RESPONSE = "substantive_response"
    LEARNER_REASONED_CONCLUSION = "learner_reasoned_conclusion"
    DIRECT_ANSWER_REQUESTED = "direct_answer_requested"
    EXIT_CONFIRMED = "exit_confirmed"
    EXIT_DECLINED = "exit_declined"
    EXPLICIT_FRUSTRATION = "explicit_frustration"
    OFF_TOPIC = "off_topic"
    CAP_REACHED = "cap_reached"
    LOOP_DETECTED = "loop_detected"
    MODE_TOGGLED_OFF = "mode_toggled_off"


class Mode(str, Enum):
    """SPECIFIED by the company; closed per integration brief §4.2.

    Replaces the fixed literal ``"socratic"`` that the interaction log record
    used to carry, so a reader sees a value from a known set rather than a
    constant that could only ever mean one thing.

    KNOWN LIMITATION - flagged in brief §4.2 and deliberately left open. This
    single field conflates *session type* (``free_form`` / ``course_linked`` /
    ``case_linked``, owned by UC-01) with *response mode* (``socratic``). A
    Socratic turn therefore cannot carry the session type underneath it, and
    UC-05 can only ever write ``SOCRATIC`` because nothing tells it the session
    type. Whether to split this into two orthogonal fields is an open platform
    decision recorded in PLATFORM_CONTRACT.md §8. It is not resolved here.
    """

    FREE_FORM = "free_form"
    COURSE_LINKED = "course_linked"
    CASE_LINKED = "case_linked"
    SOCRATIC = "socratic"


class ModeSource(str, Enum):
    """ASSUMED by us (A-MODE-DEFAULT)."""

    PERSISTED = "persisted"
    DEFAULT = "default"
