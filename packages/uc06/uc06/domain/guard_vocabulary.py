"""Guard vocabularies and injection patterns.

These are DOMAIN CONSTANTS, not configuration. They live in code, in a frozen
module-level structure, and no environment variable, request field or admin
setting reaches them. The specification states the redirect is always the correct
response in coaching mode; there is therefore nothing to configure.

Adding or refining a phrase is a code change, reviewed like any other.
Phrase sets are an assumption - see docs/assumptions.md row A-04.
"""

from __future__ import annotations

import re
from typing import Final

from .enums import GuardClass


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


#: (rule_id, compiled pattern, guard class)
GUARD_RULES: Final[tuple[tuple[str, re.Pattern[str], GuardClass], ...]] = (
    # ---- outcome prediction -------------------------------------------------
    ("OP-01", _rx(r"\bwill\s+(my|our|the)\s+(client|defendant|claimant|case|appeal)\b.*\b(win|succeed|lose|fail)\b"), GuardClass.OUTCOME_PREDICTION),
    ("OP-02", _rx(r"\bwhat\s+are\s+(our|my|the)\s+(chances|odds|prospects)\b"), GuardClass.OUTCOME_PREDICTION),
    ("OP-03", _rx(r"\bhow\s+(strong|good|weak)\s+is\s+(our|my|the|this)\s+(case|defence|defense|claim|position)\b"), GuardClass.OUTCOME_PREDICTION),
    ("OP-04", _rx(r"\b(will|would)\s+(this|the|my|our)\s+(defence|defense|argument|claim|application)\s+(succeed|work|win|fail)\b"), GuardClass.OUTCOME_PREDICTION),
    ("OP-05", _rx(r"\b(are\s+we|am\s+i|is\s+(my|our)\s+client)\s+(likely\s+to|going\s+to)\s+(win|lose|succeed|be\s+convicted|be\s+acquitted)\b"), GuardClass.OUTCOME_PREDICTION),
    ("OP-06", _rx(r"\bis\s+(this|it)\s+worth\s+(defending|fighting|appealing|running)\b"), GuardClass.OUTCOME_PREDICTION),
    ("OP-07", _rx(r"\b(what|which)\s+(sentence|outcome|verdict|damages)\s+(will|would)\s+(my|our|the)\s+(client|defendant|court|judge)\b"), GuardClass.OUTCOME_PREDICTION),
    ("OP-08", _rx(r"\b(percent|percentage|%)\s*(chance|likelihood)\b|\blikelihood\s+of\s+(success|acquittal|conviction)\b"), GuardClass.OUTCOME_PREDICTION),
    # ---- litigation strategy ------------------------------------------------
    ("LS-01", _rx(r"\bshould\s+(we|i|my\s+client|the\s+defendant)\s+(plead|settle|accept|contest|appeal|testify|give\s+evidence)\b"), GuardClass.LITIGATION_STRATEGY),
    ("LS-02", _rx(r"\bwhat\s+should\s+(we|i)\s+argue\b|\bwhat\s+argument\s+should\s+(we|i)\s+run\b"), GuardClass.LITIGATION_STRATEGY),
    ("LS-03", _rx(r"\bdo\s+we\s+settle\b|\bshould\s+we\s+take\s+the\s+(deal|offer)\b"), GuardClass.LITIGATION_STRATEGY),
    ("LS-04", _rx(r"\bwhat(?:'s|s|\s+is|\s+are)?\s+(?:our|my|the)\s+best\s+(?:strategy|tactics?|approach|move|options?|course)\b"), GuardClass.LITIGATION_STRATEGY),
    ("LS-05", _rx(r"\b(which|what)\s+(defence|defense)\s+should\s+(we|i)\s+(run|use|raise)\b"), GuardClass.LITIGATION_STRATEGY),
    ("LS-06", _rx(r"\b(advise|tell)\s+(me|us)\s+what\s+to\s+do\b|\bwhat\s+do\s+you\s+recommend\s+we\s+do\b"), GuardClass.LITIGATION_STRATEGY),
    ("LS-07", _rx(r"\bshould\s+(we|i)\s+(call|not\s+call)\s+(the\s+)?(defendant|client|witness)\b"), GuardClass.LITIGATION_STRATEGY),
)


#: Attempts to suppress, alter, hide or relocate the disclaimer. Matching text is
#: DATA to be logged as a security incident - never an instruction to obey.
INJECTION_RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("INJ-01", _rx(r"\b(omit|remove|drop|delete|strip|suppress|exclude)\b[^.]{0,60}\bdisclaimer\b")),
    ("INJ-02", _rx(r"\bdisclaimer\b[^.]{0,60}\b(not\s+needed|unnecessary|off|disabled|skip(ped)?)\b")),
    ("INJ-03", _rx(r"\bwithout\s+(the\s+|any\s+)?(legal\s+)?disclaimer\b|\bno\s+disclaimer\b")),
    ("INJ-04", _rx(r"\b(hide|conceal|move|relocate|bury|shorten|truncate|replace|rewrite|reword)\b[^.]{0,60}\bdisclaimer\b")),
    ("INJ-05", _rx(r"\bignore\s+(all\s+|any\s+|your\s+|the\s+)?(previous|prior|above|system)\s+(instructions?|prompts?|rules?)\b")),
    ("INJ-06", _rx(r"\b(you\s+are\s+now|act\s+as|pretend\s+to\s+be|from\s+now\s+on\s+you\s+are)\b[^.]{0,60}\b(lawyer|solicitor|barrister|counsel|advisor|adviser)\b")),
    ("INJ-07", _rx(r"\b(this|it)\s+is\s+(real\s+)?legal\s+advice\b|\bgive\s+me\s+(actual|real|proper)\s+legal\s+advice\b")),
    ("INJ-08", _rx(r"\bdisclaimer\s*[:=]\s*(\"\"|''|none|null|false|\s*$)")),
    ("INJ-09", _rx(r"\b(developer|admin|system)\s+mode\b|\boverride\s+(the\s+)?(safety|guardrails?|policy)\b")),
    ("INJ-10", _rx(r"\b(end|close|terminate)\s+(the\s+)?response\s+before\s+the\s+disclaimer\b")),
)


#: Request field names whose presence is treated as an attempt to influence a
#: control UC-06 owns. Rejected by schema, then recorded as a security incident.
SUPPRESSION_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "disclaimer",
        "disclaimers",
        "suppress_disclaimer",
        "skip_disclaimer",
        "no_disclaimer",
        "include_disclaimer",
        "disclaimer_enabled",
        "guard_triggered",
        "guard",
        "disable_guard",
        "allow_outcome_prediction",
        "system_prompt",
        "prompt",
        "prompt_override",
        "naric_level",
        "explanation_profile",
        "user_id",
    }
)


#: Keys whose presence in an OUTGOING payload can only mean the disclaimer was
#: being suppressed or overridden somewhere upstream. Narrower than
#: SUPPRESSION_FIELD_NAMES on purpose: `guard_triggered` and
#: `explanation_profile` are legitimate response fields that a client merely may
#: not send, whereas none of these has any legitimate reason to exist at all.
DISCLAIMER_SUPPRESSION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "suppress_disclaimer",
        "skip_disclaimer",
        "no_disclaimer",
        "hide_disclaimer",
        "omit_disclaimer",
        "disclaimer_enabled",
        "disclaimer_disabled",
        "include_disclaimer",
        "disclaimer_override",
        "disclaimer_suppressed",
        "disclaimers",
    }
)


#: Phrases that would make a generated explanation read as a prediction or as
#: advice. Checked against GENERATED output at the boundary (section 6.3).
OUTPUT_PREDICTION_RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("OUT-01", _rx(r"\byou\s+(will|are\s+likely\s+to)\s+(win|lose|succeed|fail)\b")),
    ("OUT-02", _rx(r"\b(your|the)\s+(client|defence|defense|case|claim)\s+(will|is\s+likely\s+to|should)\s+(win|succeed|be\s+acquitted|fail|lose)\b")),
    ("OUT-03", _rx(r"\bi\s+(advise|recommend)\s+(you|that\s+you)\b|\byou\s+should\s+(plead|settle|appeal|accept\s+the\s+offer)\b")),
    ("OUT-04", _rx(r"\bthere\s+is\s+a\s+\d{1,3}\s*%\s*(chance|likelihood|probability)\b")),
    ("OUT-05", _rx(r"\bthe\s+court\s+will\s+(find|hold|acquit|convict|rule)\b")),
)


def classify_question(question: str) -> tuple[GuardClass, str | None]:
    for rule_id, pattern, guard_class in GUARD_RULES:
        if pattern.search(question):
            return guard_class, rule_id
    return GuardClass.NONE, None


def detect_injection(text: str) -> tuple[str, ...]:
    return tuple(rule_id for rule_id, pattern in INJECTION_RULES if pattern.search(text))


def detect_output_prediction(text: str) -> tuple[str, ...]:
    return tuple(rule_id for rule_id, pattern in OUTPUT_PREDICTION_RULES if pattern.search(text))
