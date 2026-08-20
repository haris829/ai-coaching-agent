"""The assessor review queue (§13).

    PASS  ->  persist PENDING_REVIEW  ->  publish to the queue
                     (durable)                    |
                                                  +-- unavailable? the review is still there,
                                                      still listed, still reviewable, and the
                                                      certificate is still blocked. Retriable.

**The queue is a notification, not the assessment.** Everything an assessor needs is reachable from
the review repository through the API; publishing tells their tooling that something arrived. That
is why a queue outage in §13 is recoverable by construction rather than by careful error handling:
the failure mode "the assessment disappeared" does not exist, because the assessment was never in
the queue.

NO NEW INFRASTRUCTURE
---------------------
No broker is introduced here — no Redis, no Kafka, no scheduler — because none of the existing use
cases uses one. UC-05 established the pattern for background work in this codebase (a port with an
in-process default and a manually driven implementation for tests) and this follows it. The company
binds its own queue at integration; the retry surface it needs already exists in
``services.queue_recovery_service``.

ONE ENTRY PER REVIEW
--------------------
``ReviewQueueEntry.entry_key`` is derived from the review id, which is itself unique per formal
attempt. An implementation that de-duplicates on the key cannot enqueue the same pending assessment
twice (§20), even if a retry races with the original publish.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReviewQueueEntry:
    """The notification payload: enough for an assessor's tooling to route and prioritise.

    Deliberately thin. It carries no answers, no personal data and no score breakdown — a queue is
    often a less-guarded place than a database, and everything worth protecting is one authorised
    read away for whoever picks the item up.
    """

    review_id: str
    formal_attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    attempt_id: str
    created_at: str
    percentage: float | None = None
    auto_submitted: bool = False
    anomaly_count: int = 0

    @property
    def entry_key(self) -> str:
        """The de-duplication key. One per review, therefore one per formal attempt."""
        return f"formal-review:{self.review_id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "formal_attempt_id": self.formal_attempt_id,
            "learner_id": self.learner_id,
            "course_id": self.course_id,
            "quiz_id": self.quiz_id,
            "attempt_id": self.attempt_id,
            "created_at": self.created_at,
            "percentage": self.percentage,
            "auto_submitted": self.auto_submitted,
            "anomaly_count": self.anomaly_count,
            "entry_key": self.entry_key,
        }


class ReviewQueueError(Exception):
    """The queue could not accept the entry. Always treated as transient by the caller."""


@runtime_checkable
class ReviewQueuePublisher(Protocol):
    """The assessor review queue, as UC-09 uses it."""

    async def publish(self, entry: ReviewQueueEntry) -> bool:
        """Publish one pending review.

        Returns ``True`` when the queue accepted it and ``False`` when an identical entry was
        already queued — both are success, and the caller records the review as published either
        way. Raise :class:`ReviewQueueError` when the queue could not be reached, which is what
        leaves the review recoverable.
        """
        ...


class InMemoryReviewQueue:
    """Provisional in-process queue.

    **Not durable, and not pretending to be.** It exists so a standalone deployment of UC-09 works
    end to end and so tests can assert what was published. Durability is not what makes §13 hold —
    the review record is — so an in-memory queue is an honest default rather than a dangerous one:
    losing its contents loses notifications, and the recovery sweep republishes them. """

    def __init__(self) -> None:
        self.entries: list[ReviewQueueEntry] = []
        self._keys: set[str] = set()
        self._lock = asyncio.Lock()
        #: Set by a test (or an operator experiment) to make the queue unavailable.
        self.unavailable: bool = False
        self.failure_message: str = "The assessor review queue is unavailable."

    async def publish(self, entry: ReviewQueueEntry) -> bool:
        if self.unavailable:
            raise ReviewQueueError(self.failure_message)
        async with self._lock:
            if entry.entry_key in self._keys:
                logger.info("formal.queue.duplicate_suppressed", extra={"key": entry.entry_key})
                return False
            self._keys.add(entry.entry_key)
            self.entries.append(entry)
            return True

    def pending_count(self) -> int:
        return len(self.entries)

    def keys(self) -> list[str]:
        return [entry.entry_key for entry in self.entries]
