"""The attempt-rules boundary. Everything UC-05 needs about the attempt itself, and nothing more:
the pass mark and maximum attempts of the version the attempt was locked to, how many attempts
the learner has used, and the course and quiz names a certificate and a CPD record have to carry.
Two decisions are worth naming. **The rules come from the attempt, not from the quiz.**
``pass_mark_percentage`` and ``max_attempts`` are read out of the configuration snapshot UC-03
froze onto the attempt at creation. That is what makes a mid-course reconfiguration unable to
move the bar under a learner who has already sat the quiz. **There is one attempt counter.**
``attempts_used`` is UC-03's count of that learner's attempts at that quiz -- the same number
UC-01's rules summary and UC-03's eligibility check report. UC-05 reads it through this port
rather than keeping a second count of its own, exactly as UC-01 does."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = ["AttemptPolicy", "AttemptPolicyPort"]


@dataclass(frozen=True, slots=True)
class AttemptPolicy:
    """The attempt's own rules, plus the names its downstream records need."""

    attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    attempt_number: int
    configuration_version_id: str

    #: From the attempt's frozen configuration snapshot.
    pass_mark_percentage: float
    #: ``None`` when the configuration sets no maximum.
    max_attempts: int | None
    #: UC-03's count of this learner's attempts at this quiz, including this one.
    attempts_used: int

    #: Frozen names, so a rename cannot rewrite an issued certificate or a sent CPD record.
    course_name: str
    quiz_title: str | None
    submitted_at: datetime | None
    started_at: datetime | None = None


class AttemptPolicyPort(Protocol):
    """Read access to an attempt's rules and the learner's attempt count."""

    def get_policy(self, attempt_id: str, *, learner_id: str | None = None) -> AttemptPolicy | None:
        """The attempt's rules, or ``None`` when it does not exist or is not this learner's."""
        ...
