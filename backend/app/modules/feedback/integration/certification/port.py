"""The pass/fail boundary. UC-06 reports pass/fail; it does not decide it. This port asks UC-05 for
the determination and nothing else -- there is no method here that could issue a certificate or
move an outcome. ``None`` is a legitimate answer. A report can be generated from a confirmed
score before pass/fail has been determined, in which case the report carries ``passed: null``
rather than guessing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

__all__ = ["AttemptOutcomeSummary", "OutcomePort"]


@dataclass(frozen=True, slots=True)
class AttemptOutcomeSummary:
    """UC-05's determination for one attempt."""

    outcome_id: str
    attempt_id: str
    #: ``PASS`` or ``FAIL``, as UC-05 recorded it.
    outcome: str
    passed: bool
    percentage: float
    pass_mark_percentage: float


class OutcomePort(Protocol):
    """Read access to UC-05's determined outcomes."""

    def get_outcome(self, attempt_id: str) -> AttemptOutcomeSummary | None: ...
