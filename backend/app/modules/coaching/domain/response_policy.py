"""Checking the coach's reply before a learner sees it (§14, §24, §29).

The sanitiser guarantees the model never *had* the answer key. This module guarantees the model
does not behave as though it did.

The two are different failures. A model with no answer key can still ruin a coaching session by
guessing an answer and announcing it ("The correct answer is B"), or by claiming an authority it
does not have ("According to the answer key…"). Neither leaks anything — there is nothing to leak
— but both destroy the teaching, and the second is a lie to the learner about how the system works.

WHAT HAPPENS ON A VIOLATION
---------------------------
The reply is **discarded**, the model is asked again with the policy restated, and if it violates
again the exchange fails with a controlled error (§27). It is never patched up, never truncated
into shape and never replaced with a canned message: a fixed string dressed as coaching is exactly
the fake chatbot §6 rules out.

WHY "CONTAINS A QUESTION" IS A RULE
-----------------------------------
Socratic coaching is defined by asking (§14). A Socratic turn with no question in it is a lecture,
and a coach that lectures for five turns has quietly skipped the learner past the choice §15 gives
them. The check is deliberately weak — *some* question mark, anywhere — so it catches a reply that
has abandoned the method without dictating how the coach should phrase itself.

The rule applies to SOCRATIC only. In DIRECT_EXPLANATION the learner has explicitly asked to be
told, and requiring a question there would be arguing with them (§16).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.modules.coaching.domain.enums import CoachingMode

#: Announcing an answer. Requires a value after the assertion, so a coach *asking* "which do you
#: think is the correct answer?" is untouched.
_ANSWER_ASSERTION = re.compile(
    r"(?i)\b(?:the\s+)?(?:correct|right)\s+(?:answer|option|choice|response)s?\s*"
    r"(?:is|are|was|were|:)\s*\S+"
)

#: Claiming access to hidden assessment data. Always a violation, in every mode: the model has no
#: such access, so any such sentence is false (§24).
_CLAIMED_KEY_ACCESS = re.compile(
    r"(?i)\b(?:according to|based on|from|per|i (?:have|can see|was given)(?:\s+\w+){0,3})\s+"
    r"(?:the\s+)?(?:answer[\s_-]?key|marking[\s_-]?(?:key|scheme)|scoring[\s_-]?key|"
    r"grading[\s_-]?key|(?:hidden|internal)\s+(?:answer|metadata|data))"
)

#: A blunt second net for the same claim phrased the other way round.
_CLAIMED_KEY_ACCESS_ALT = re.compile(
    r"(?i)\b(?:answer[\s_-]?key|marking[\s_-]?scheme|hidden\s+metadata)\b[^.?!\n]*\b"
    r"(?:says|shows|states|tells me|indicates|records)\b"
)

VIOLATION_ANSWER_REVEALED = "ANSWER_REVEALED"
VIOLATION_CLAIMED_KEY_ACCESS = "CLAIMED_ANSWER_KEY_ACCESS"
VIOLATION_NO_GUIDING_QUESTION = "NO_GUIDING_QUESTION"

INVALID_EMPTY = "EMPTY"
INVALID_NOT_TEXT = "NOT_TEXT"
INVALID_TOO_LONG = "TOO_LONG"


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    """Whether one model reply may be shown to the learner."""

    #: Populated when the reply is not usable at all — empty, non-textual, absurdly long.
    invalid_reason: str | None = None
    #: Populated when the reply is usable text but breaks the coaching policy.
    violations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        return self.invalid_reason is None and not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {"invalid_reason": self.invalid_reason, "violations": list(self.violations)}


_OK = PolicyVerdict()


def evaluate_response(
    text: object, *, mode: CoachingMode, max_chars: int = 4000
) -> PolicyVerdict:
    """Judge one model reply.

    Structural validity is checked first: there is no point asking whether an empty string was
    Socratic.
    """
    if not isinstance(text, str):
        return PolicyVerdict(invalid_reason=INVALID_NOT_TEXT)

    stripped = text.strip()
    if not stripped:
        return PolicyVerdict(invalid_reason=INVALID_EMPTY)
    if len(stripped) > max_chars:
        return PolicyVerdict(invalid_reason=INVALID_TOO_LONG)

    violations: list[str] = []
    if _CLAIMED_KEY_ACCESS.search(stripped) or _CLAIMED_KEY_ACCESS_ALT.search(stripped):
        violations.append(VIOLATION_CLAIMED_KEY_ACCESS)

    if mode is CoachingMode.SOCRATIC:
        if _ANSWER_ASSERTION.search(stripped):
            violations.append(VIOLATION_ANSWER_REVEALED)
        if "?" not in stripped:
            violations.append(VIOLATION_NO_GUIDING_QUESTION)

    return _OK if not violations else PolicyVerdict(violations=tuple(violations))
