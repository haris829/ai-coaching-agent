"""The CPD synchronisation boundary. Continuing Professional Development records live in a system
UC-05 does not own. The contract is the four facts that system needs, and no more: **attempt
date, score, pass/fail and course name**. The important property is stated in the requirement and
enforced by the service: **a CPD failure never changes the quiz result**. That is why
synchronisation happens after the outcome is durable, why a transient failure only leaves a
``PENDING`` CPD row, and why nothing on this port can reach back into a score or an outcome."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = ["CpdSyncAck", "CpdSyncPort", "CpdSyncRecord", "TransientCpdError"]


@dataclass(frozen=True, slots=True)
class CpdSyncRecord:
    """The CPD event for one completed attempt."""

    attempt_id: str
    learner_id: str
    course_id: str
    #: ISO-8601 UTC instant the attempt was submitted.
    attempt_date: str
    #: Percentage score, as scored by UC-04.
    score_percentage: float
    passed: bool
    course_name: str
    total_marks: float = 0.0
    maximum_marks: float = 0.0
    #: Stable key, so a retried synchronisation is recognisable as the same event.
    idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class CpdSyncAck:
    """Acknowledgement from the CPD system."""

    external_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TransientCpdError(Exception):
    """Raised when synchronisation failed for a reason that may succeed on a retry."""


class CpdSyncPort(Protocol):
    """Push one CPD record to the CPD system."""

    def synchronise(self, record: CpdSyncRecord) -> CpdSyncAck: ...
