"""Enrolment boundary.

Course enrolment is owned by the wider Courses platform, not by UC-03. UC-03 only
asks a yes/no eligibility question before it creates an attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.modules.attempt_delivery.domain.enums import EnrolmentStatus


@dataclass(frozen=True, slots=True)
class Enrolment:
    learner_id: str
    course_id: str
    status: EnrolmentStatus
    enrolled_at: str


class EnrolmentPort(Protocol):
    """Read access to course enrolment."""

    def get_enrolment(self, learner_id: str, course_id: str) -> Enrolment | None:
        """Return the enrolment, or ``None`` when the learner has no record."""
        ...
