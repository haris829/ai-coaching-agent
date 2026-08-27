"""The documented, deterministic rules behind intent classification.

These live in the domain rather than in an adapter because they *are* the
specified behaviour of UC-05 ("detection is on explicit statements only",
"frustration and casual difficulty must be separable"), not an implementation
detail of any particular classifier.  The mock classifier applies them
directly.  A real classifier adapter is free to use a model instead, but it
must still produce the same ``IntentKind`` vocabulary, and the conformance
suite checks that it does.

Precedence, applied in this order and documented as A-INTENT-PRECEDENCE:

1.  ``explicit_frustration``  -- a whole clause matches the explicit set AND
    the message carries no substantive reasoning alongside it.
2.  ``direct_answer_request`` -- an unambiguous request phrase appears.
3.  ``exit_confirmation`` / ``exit_declined`` -- a whole clause matches.
4.  ``learner_reasoned_conclusion`` -- a conclusion marker appears.
5.  ``casual_difficulty``     -- a whole clause matches the casual set.
6.  ``off_topic``             -- a whole clause matches the off-topic set.
7.  ``substantive_response``  -- everything else.

Step 1's second condition is what stops "I'm stuck, but is it because
consideration must move from the promisee?" from rescuing a learner who is
still reasoning.  A clause counts as substantive when it is neither an
explicit-frustration nor a casual-difficulty phrase and carries at least
``SUBSTANTIVE_TOKEN_THRESHOLD`` content tokens.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import normalisation as norm
from . import vocabulary as vocab
from .enums import IntentKind

# A-FRUSTRATION-RULE: three content tokens is enough to distinguish "sorry"
# from "consideration must move from the promisee".
SUBSTANTIVE_TOKEN_THRESHOLD = 3


@dataclass(frozen=True)
class RuleOutcome:
    """The classification plus the evidence that produced it.

    ``matched_phrase`` is drawn from *our* configured vocabulary, never from
    the learner's free text, so it is safe to log.
    """

    kind: IntentKind
    matched_phrase: str | None
    rule: str


def _whole_clause_match(message: str, phrases: tuple[str, ...]) -> str | None:
    clause_set = set(norm.clauses(message))
    for phrase in phrases:
        if phrase in clause_set:
            return phrase
    return None


def _containment_match(message: str, phrases: tuple[str, ...]) -> str | None:
    flattened = norm.flatten(message)
    for phrase in phrases:
        if phrase in flattened:
            return phrase
    return None


def has_substantive_clause(message: str) -> bool:
    """Does the message carry reasoning alongside any stock phrase?"""
    for clause in norm.clauses(message):
        if clause in vocab.EXPLICIT_FRUSTRATION_PHRASES:
            continue
        if clause in vocab.CASUAL_DIFFICULTY_PHRASES:
            continue
        if len(norm.content_tokens(clause)) >= SUBSTANTIVE_TOKEN_THRESHOLD:
            return True
    return False


def classify_message(message: str) -> RuleOutcome:
    """Apply the documented precedence.  Pure, deterministic, model-free."""
    frustration = _whole_clause_match(message, vocab.EXPLICIT_FRUSTRATION_PHRASES)
    if frustration and not has_substantive_clause(message):
        return RuleOutcome(IntentKind.EXPLICIT_FRUSTRATION, frustration, "explicit_clause")

    request = _containment_match(message, vocab.DIRECT_ANSWER_REQUEST_PHRASES)
    if request:
        return RuleOutcome(IntentKind.DIRECT_ANSWER_REQUEST, request, "containment")

    decline = _whole_clause_match(message, vocab.EXIT_DECLINE_PHRASES)
    if decline:
        return RuleOutcome(IntentKind.EXIT_DECLINED, decline, "whole_clause")

    confirm = _whole_clause_match(message, vocab.EXIT_CONFIRMATION_PHRASES)
    if confirm:
        return RuleOutcome(IntentKind.EXIT_CONFIRMATION, confirm, "whole_clause")

    conclusion = _containment_match(message, vocab.CONCLUSION_MARKERS)
    if conclusion:
        return RuleOutcome(
            IntentKind.LEARNER_REASONED_CONCLUSION, conclusion, "containment"
        )

    casual = _whole_clause_match(message, vocab.CASUAL_DIFFICULTY_PHRASES)
    if casual:
        return RuleOutcome(IntentKind.CASUAL_DIFFICULTY, casual, "whole_clause")

    off_topic = _whole_clause_match(message, vocab.OFF_TOPIC_PHRASES)
    if off_topic:
        return RuleOutcome(IntentKind.OFF_TOPIC, off_topic, "whole_clause")

    return RuleOutcome(IntentKind.SUBSTANTIVE_RESPONSE, None, "default")
