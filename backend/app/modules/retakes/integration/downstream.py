"""The read-only ports onto everything that happens *after* an attempt: UC-04, UC-05, UC-06 and
UC-07.

They are grouped in one file because UC-08 uses them for one purpose — assembling attempt history
(§9) — and because that shared purpose is what keeps them honest. Not one of these ports has a
write method, and none of them is consulted when deciding whether a retake may be created. A
learner's eligibility does not depend on their score, and creating a retake cannot touch a score,
a pass/fail decision, a feedback report or a coaching conversation.

::

    UC-04  score, maximum, percentage  ─┐
    UC-05  pass / fail                  ├──▶ attempt history entry (read-only)
    UC-06  is there a feedback report?  │
    UC-07  is coaching available?      ─┘

Every field is optional and every provider may return ``None``. An attempt whose score has not
been confirmed yet still appears in the history — with a labelled gap rather than a fabricated
number. UC-06 does the same thing for a missing explanation, and for the same reason: a report
that says "not available" is worth more than one that invents a value.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# UC-04 — Answer Validation & Scoring
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttemptScore:
    """UC-04's confirmed score for one attempt.

    Carried through untouched. UC-08 performs no scoring arithmetic whatsoever — not even
    recomputing a percentage from a total and a maximum, because a percentage that disagreed with
    UC-04's would make two answers to one question.
    """

    attempt_id: str
    #: True only when UC-04 has confirmed the score. A pending score is reported as pending.
    confirmed: bool
    total_marks: float | None = None
    maximum_marks: float | None = None
    percentage: float | None = None
    scored_at: str | None = None


@runtime_checkable
class ScoringResultProvider(Protocol):
    async def get_score(self, attempt_id: str) -> AttemptScore | None:
        """The confirmed score, or ``None`` when UC-04 has none for this attempt."""
        ...


# ---------------------------------------------------------------------------
# UC-05 — Pass / Fail & Certificate Gating
# ---------------------------------------------------------------------------


class PassFailStatus(StrEnum):
    """UC-05's vocabulary, mirrored. UC-08 makes no pass/fail decision of its own."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    #: Determined but blocked by an upstream defect — UC-05's own pending state.
    PENDING = "PENDING"


@dataclass(frozen=True, slots=True)
class PassFailResult:
    attempt_id: str
    status: PassFailStatus
    pass_mark_percentage: float | None = None
    determined_at: str | None = None


@runtime_checkable
class PassFailResultProvider(Protocol):
    async def get_result(self, attempt_id: str) -> PassFailResult | None:
        """The pass/fail result, or ``None`` when UC-05 has not determined one."""
        ...


# ---------------------------------------------------------------------------
# UC-06 — Detailed Feedback Report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeedbackAvailability:
    """Whether a feedback report exists for an attempt.

    Only availability crosses this boundary — not the report. A history listing needs to know
    whether there is something to link to; it does not need the question-level feedback, and
    pulling it here would put UC-06's content in a second place.
    """

    attempt_id: str
    available: bool
    status: str | None = None
    generated_at: str | None = None


@runtime_checkable
class FeedbackProvider(Protocol):
    async def get_feedback_availability(self, attempt_id: str) -> FeedbackAvailability | None:
        ...


# ---------------------------------------------------------------------------
# UC-07 — AI Coaching Review Mode
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoachingAvailability:
    """Whether coaching is available for an attempt, and how much has been done.

    Read only so history can show it. A retake never copies, moves, closes or continues a
    coaching conversation: the previous attempt's coaching stays attached to the previous
    attempt, which is the attempt it is about.
    """

    attempt_id: str
    available: bool
    coachable_question_count: int = 0
    completed_session_count: int = 0


@runtime_checkable
class CoachingProvider(Protocol):
    async def get_coaching_availability(self, attempt_id: str) -> CoachingAvailability | None:
        ...
