"""Recovering from an assessor-queue outage (§13).

    PASS -> PENDING_REVIEW persisted -> queue failure -> recoverable
                                                              |
                                              list_recoverable / retry / sweep

The recovery surface, and the reason §13's failure mode cannot lose an assessment: the review is
durable before the queue is touched, so an outage produces a *work list* rather than a gap. Three
operations:

* :meth:`list_recoverable` — every review the queue has not accepted, oldest first. What an
  at, and what a monitor alerts on.
* :meth:`retry` — publish one review again, and say plainly whether it worked.
* :meth:`sweep` — retry all of them, for a scheduled job or an operator's single click.

WHAT A RETRY CANNOT DO
----------------------
It cannot approve anything, cannot unblock a certificate and cannot change a review's decision
state. It publishes a notification. That separation is why running the sweep repeatedly is safe: the
worst outcome of a redundant retry is a duplicate queue entry, which the entry key already prevents
(§20).

NO NEW INFRASTRUCTURE
---------------------
There is no scheduler here and no broker. ``sweep`` is a method the company's existing job runner,
cron, or an operator endpoint calls. Introducing a queue to fix a queue outage would be an odd way
to spend a dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.modules.formal_assessment.domain.errors import ReviewQueueUnavailableError
from app.modules.formal_assessment.domain.review import FormalReview
from app.modules.formal_assessment.services.review_service import FormalReviewService

logger = get_logger(__name__)

#: Upper bound on one sweep, so a large backlog is worked through in batches rather than in one call
#: that
#: holds a connection open for minutes.
MAX_SWEEP_BATCH = 100


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """What one sweep achieved."""

    considered: int
    published: int
    still_pending: int
    review_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "considered": self.considered,
            "published": self.published,
            "still_pending": self.still_pending,
            "review_ids": list(self.review_ids),
        }


class ReviewQueueRecoveryService:
    def __init__(self, *, reviews: FormalReviewService, batch_size: int = MAX_SWEEP_BATCH) -> None:
        self._reviews = reviews
        self._batch_size = max(1, min(batch_size, MAX_SWEEP_BATCH))

    async def list_recoverable(self, *, limit: int = MAX_SWEEP_BATCH) -> tuple[FormalReview, ...]:
        """Reviews the queue has not accepted. None of them is lost; all of them are actionable."""
        return await self._reviews.list_unpublished(limit=max(1, min(limit, MAX_SWEEP_BATCH)))

    async def retry(self, review_id: str) -> FormalReview:
        """Publish one review again (§13).

        Raises ``ReviewQueueUnavailableError`` when the queue is still down — the caller asked to
        publish and deserves to know it did not happen. The review remains recoverable either way.
        """
        return await self._reviews.retry_publish(review_id)

    async def sweep(self) -> RecoveryReport:
        """Retry every recoverable review, and report what happened.

        Does not raise on a queue that is still down: a sweep over twenty reviews should not stop at
        the first failure, and the report is what tells the operator whether to try again later.
        """
        pending = await self.list_recoverable(limit=self._batch_size)
        published: list[str] = []
        still_pending: list[str] = []

        for review in pending:
            try:
                updated = await self.retry(review.review_id)
            except ReviewQueueUnavailableError:
                still_pending.append(review.review_id)
                continue
            except Exception as error:  # noqa: BLE001 - one bad review must not stop the sweep
                logger.warning(
                    "formal.queue.retry_failed",
                    extra={"review_id": review.review_id, "error": type(error).__name__},
                )
                still_pending.append(review.review_id)
                continue
            if updated.awaiting_publish:
                still_pending.append(review.review_id)
            else:
                published.append(review.review_id)

        return RecoveryReport(
            considered=len(pending),
            published=len(published),
            still_pending=len(still_pending),
            review_ids=tuple(published),
        )
