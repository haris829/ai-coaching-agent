"""The confirmed-score boundary. UC-05 gates on UC-04's result and nothing else. It never scores,
never re-scores, and cannot reach a question or an answer through this port -- the only thing it
can ask is "what did this attempt score, and was that score confirmed?". ``confirmed`` is a field
rather than something UC-05 infers from a status string, so UC-04 keeps ownership of what
"confirmed" means. Gating on an unconfirmed score is refused, which is the whole reason the flag
is here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = ["ConfirmedResult", "ScoreResultPort"]


@dataclass(frozen=True, slots=True)
class ConfirmedResult:
    """One attempt's score, as UC-05 needs to see it."""

    result_id: str
    attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    attempt_number: int
    configuration_version_id: str

    percentage: float
    total_marks: float
    maximum_marks: float
    #: The pass mark UC-04 froze from the attempt's own configuration version.
    pass_mark_percentage: float

    #: UC-04's status verbatim, for diagnostics.
    status: str
    #: True only when UC-04 has confirmed the score. A pending score cannot be gated on.
    confirmed: bool
    submitted_at: datetime | None = None


class ScoreResultPort(Protocol):
    """Read access to UC-04's results."""

    def get_result(self, attempt_id: str) -> ConfirmedResult | None:
        """The stored result for an attempt, or ``None`` when scoring has not recorded one."""
        ...
