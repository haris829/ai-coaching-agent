"""Attempt history (§9).

A read model, assembled from the modules that own each fact and stored nowhere::

    UC-03  attempt, number, dates, configuration version
    UC-04  score, maximum, percentage
    UC-05  pass / fail
    UC-06  is there a feedback report?
    UC-07  is coaching available?
    UC-08  which attempt was a retake of which

**Nothing here is written.** UC-08 has no repository method that can change a historical attempt,
no method that can change a score, and no method that can change a pass/fail status. Creating a
retake adds a new attempt to the end of this list and changes nothing already in it (§3) — a
property the history tests assert by snapshotting the list before a retake and comparing it
after.

Missing upstream data is *labelled*, never filled in. An attempt whose score UC-04 has not
confirmed appears with ``score_available: false`` rather than with a zero, for the same reason
UC-06 refuses to invent an explanation: a fabricated zero in a history table is indistinguishable
from a genuine zero, and a learner would read it as a failed attempt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AttemptHistoryEntry:
    """One attempt, as history shows it."""

    attempt_id: str
    attempt_number: int
    status: str
    configuration_version_id: str
    configuration_version_number: int | None = None
    started_at: str | None = None
    submitted_at: str | None = None
    total_questions: int | None = None

    # ---- UC-04, carried through untouched -------------------------------
    score_available: bool = False
    total_marks: float | None = None
    maximum_marks: float | None = None
    percentage: float | None = None

    # ---- UC-05 ----------------------------------------------------------
    pass_fail_available: bool = False
    pass_fail_status: str | None = None
    pass_mark_percentage: float | None = None

    # ---- UC-06 / UC-07 --------------------------------------------------
    feedback_available: bool = False
    coaching_available: bool = False

    # ---- UC-08's own contribution: the retake relationship (§10) --------
    is_retake: bool = False
    retake_of_attempt_id: str | None = None
    retake_id: str | None = None
    #: Set on the attempt that was *followed by* a retake, so the relationship reads in both
    #: directions without a second query.
    retaken_by_attempt_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "status": self.status,
            "configuration_version_id": self.configuration_version_id,
            "configuration_version_number": self.configuration_version_number,
            "started_at": self.started_at,
            "submitted_at": self.submitted_at,
            "total_questions": self.total_questions,
            "score_available": self.score_available,
            "total_marks": self.total_marks,
            "maximum_marks": self.maximum_marks,
            "percentage": self.percentage,
            "pass_fail_available": self.pass_fail_available,
            "pass_fail_status": self.pass_fail_status,
            "pass_mark_percentage": self.pass_mark_percentage,
            "feedback_available": self.feedback_available,
            "coaching_available": self.coaching_available,
            "is_retake": self.is_retake,
            "retake_of_attempt_id": self.retake_of_attempt_id,
            "retake_id": self.retake_id,
            "retaken_by_attempt_id": self.retaken_by_attempt_id,
        }


@dataclass(frozen=True, slots=True)
class AttemptHistory:
    """Every attempt a learner has made at one quiz, oldest first."""

    learner_id: str
    quiz_id: str
    course_id: str | None
    entries: tuple[AttemptHistoryEntry, ...] = field(default_factory=tuple)

    @property
    def attempt_count(self) -> int:
        return len(self.entries)

    @property
    def latest(self) -> AttemptHistoryEntry | None:
        return self.entries[-1] if self.entries else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "quiz_id": self.quiz_id,
            "course_id": self.course_id,
            "attempt_count": self.attempt_count,
            "entries": [entry.as_dict() for entry in self.entries],
        }
