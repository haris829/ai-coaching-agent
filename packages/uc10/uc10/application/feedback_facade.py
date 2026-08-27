"""The isolation boundary between feedback and the product.

Coaching is the product; feedback is peripheral.  Any caller whose main path touches this
component calls it *here*, and this facade never raises for a feedback failure -- not for
a port error, not for a bug in this component, not for a broken collaborator.  A failure
becomes a result object carrying a retryable message.

``BaseException`` is deliberately not caught: cancellation and interpreter shutdown must
still propagate.
"""

from __future__ import annotations

from uc10.application.rating_service import RatingService
from uc10.application.results import RatingCaptureResult, RatingCaptureStatus
from uc10.domain.enums import RatingValue
from uc10.domain.models import RatingRecord
from uc10.logging_setup import get_logger

log = get_logger("uc10.feedback")


class FeedbackFacade:
    def __init__(self, ratings: RatingService) -> None:
        self._ratings = ratings

    def capture(
        self,
        *,
        interaction_id: str,
        user_id: str | None,
        rating: RatingValue,
        comment: str | None = None,
    ) -> RatingCaptureResult:
        try:
            return self._ratings.capture(
                interaction_id=interaction_id,
                user_id=user_id,
                rating=rating,
                comment=comment,
            )
        except Exception as exc:  # noqa: BLE001 - total isolation boundary
            log.error(
                "rating_capture_unexpected_failure",
                interaction_id=interaction_id,
                error_type=type(exc).__name__,
                retryable=True,
            )
            return RatingCaptureResult.of(
                RatingCaptureStatus.FAILED_RETRYABLE, reason_code="unexpected_failure"
            )

    def current_rating(self, *, interaction_id: str, user_id: str) -> RatingRecord | None:
        """None both when there is no rating and when the read failed. Reads never raise
        into a caller's main path either."""
        try:
            return self._ratings.current_rating_for(
                interaction_id=interaction_id, user_id=user_id
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "rating_read_unexpected_failure",
                interaction_id=interaction_id,
                error_type=type(exc).__name__,
            )
            return None
