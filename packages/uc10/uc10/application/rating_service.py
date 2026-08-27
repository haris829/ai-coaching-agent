"""Rating capture.

Two properties are structural here rather than incidental:

* **Nothing is unrateable.**  The response category is carried onto the rating record and
  is never consulted to decide whether a rating is allowed.  There is no category branch
  in this file, so no category can be excluded by it.
* **A feedback failure cannot reach the caller's main path.**  Every foreseeable failure
  becomes a :class:`~uc10.application.results.RatingCaptureResult`; see
  :mod:`uc10.application.feedback_facade` for the total guarantee.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from uc10.application.results import RatingCaptureResult, RatingCaptureStatus
from uc10.domain.enums import RatingValue
from uc10.domain.ids import new_rating_id
from uc10.domain.models import InteractionRecord, RatingRecord
from uc10.logging_setup import get_logger
from uc10.ports.clock import Clock
from uc10.ports.errors import PortError, ProviderInvalidResponse, RecordNotFound
from uc10.ports.interaction_provider import InteractionProvider
from uc10.ports.rating_repository import RatingRepository
from uc10.ports.threshold_config_provider import ThresholdConfigProvider

log = get_logger("uc10.rating")


class RatingService:
    def __init__(
        self,
        *,
        interactions: InteractionProvider,
        ratings: RatingRepository,
        clock: Clock,
        config: ThresholdConfigProvider,
        on_rating_recorded=None,  # callable(topic_tag) -> None; flag evaluation hook
    ) -> None:
        self._interactions = interactions
        self._ratings = ratings
        self._clock = clock
        self._config = config
        self._on_rating_recorded = on_rating_recorded

    # ------------------------------------------------------------------ capture

    def capture(
        self,
        *,
        interaction_id: str,
        user_id: str | None,
        rating: RatingValue,
        comment: str | None = None,
    ) -> RatingCaptureResult:
        """Record a thumbs up or thumbs down, replacing this learner's previous rating.

        ``comment`` is optional in every case.  A thumbs down whose comment box was
        dismissed arrives here with ``comment=None`` and is recorded exactly like any
        other rating: the rating is the signal, the comment is a bonus.
        """
        if user_id is None:
            # Authentication is a pre-condition. Nothing reaches the improvement pipeline.
            log.info(
                "rating_rejected_anonymous",
                interaction_id=interaction_id,
                rating=rating.value,
                comment_supplied=comment is not None,
            )
            return RatingCaptureResult.of(RatingCaptureStatus.REJECTED_ANONYMOUS)

        try:
            interaction = self._interactions.get(interaction_id)
        except RecordNotFound:
            log.info("rating_rejected_unknown_interaction", interaction_id=interaction_id)
            return RatingCaptureResult.of(
                RatingCaptureStatus.REJECTED_NOT_FOUND, reason_code="interaction_not_found"
            )
        except ProviderInvalidResponse as exc:
            log.warning(
                "rating_blocked_invalid_interaction",
                interaction_id=interaction_id,
                reason_code=exc.reason_code,
            )
            return RatingCaptureResult.of(
                RatingCaptureStatus.FAILED_PERMANENT, reason_code=exc.reason_code
            )
        except PortError as exc:
            log.warning(
                "rating_blocked_upstream_unavailable",
                interaction_id=interaction_id,
                reason_code=exc.reason_code,
                retryable=True,
            )
            return RatingCaptureResult.of(
                RatingCaptureStatus.FAILED_RETRYABLE, reason_code=exc.reason_code
            )

        if interaction.user_id != user_id:
            # No cross-user rating. Existence is not disclosed to the wrong learner.
            log.warning(
                "rating_rejected_cross_user",
                interaction_id=interaction_id,
                requesting_user_id=user_id,
            )
            return RatingCaptureResult.of(
                RatingCaptureStatus.REJECTED_NOT_FOUND, reason_code="not_owner"
            )

        try:
            delivered_at = self._interactions.delivered_at(interaction_id)
        except PortError as exc:
            log.warning(
                "rating_blocked_delivery_time_unavailable",
                interaction_id=interaction_id,
                reason_code=exc.reason_code,
            )
            return RatingCaptureResult.of(
                RatingCaptureStatus.FAILED_RETRYABLE, reason_code=exc.reason_code
            )

        now = self._clock.now()
        window_hours = self._config.historical_rating_window_hours()
        age = now - delivered_at
        if age > timedelta(hours=window_hours):
            # Computed server-side from the interaction record's delivery time. A
            # client-supplied timestamp cannot reach this comparison: the request schema
            # rejects one outright and no client value is read here.
            log.info(
                "rating_rejected_window_expired",
                interaction_id=interaction_id,
                topic_tag=interaction.topic_tag,
                age_hours=round(age.total_seconds() / 3600, 3),
                window_hours=window_hours,
            )
            return RatingCaptureResult.of(
                RatingCaptureStatus.REJECTED_WINDOW_EXPIRED, reason_code="window_expired"
            )
        if age < timedelta(0):
            log.warning(
                "rating_delivery_time_in_future",
                interaction_id=interaction_id,
                skew_seconds=round(-age.total_seconds(), 3),
            )

        record = self._build_record(
            interaction=interaction, rating=rating, comment=comment, rated_at=now
        )

        try:
            saved = self._ratings.save(record)
        except PortError as exc:
            log.warning(
                "rating_write_failed",
                interaction_id=interaction_id,
                topic_tag=interaction.topic_tag,
                rating=rating.value,
                reason_code=exc.reason_code,
                retryable=exc.retryable,
            )
            status = (
                RatingCaptureStatus.FAILED_RETRYABLE
                if exc.retryable
                else RatingCaptureStatus.FAILED_PERMANENT
            )
            return RatingCaptureResult.of(status, reason_code=exc.reason_code)

        superseded_id = self._supersede_previous(
            interaction_id=interaction_id, user_id=user_id, new_rating_id=saved.rating_id
        )

        log.info(
            "rating_recorded",
            rating_id=saved.rating_id,
            interaction_id=saved.interaction_id,
            session_id=saved.session_id,
            user_id=saved.user_id,
            topic_tag=saved.topic_tag,
            rating=saved.rating.value,
            naric_level=saved.naric_level.value,
            session_mode=saved.session_mode,
            response_category=interaction.response_category.value,
            comment_supplied=saved.comment is not None,
            comment_length=len(saved.comment) if saved.comment else 0,
            superseded_rating_id=superseded_id,
            rated_at=saved.rated_at.isoformat(),
        )

        self._trigger_evaluation(saved.topic_tag)

        status = (
            RatingCaptureStatus.REPLACED if superseded_id else RatingCaptureStatus.RECORDED
        )
        return RatingCaptureResult.of(status, rating=saved, superseded_rating_id=superseded_id)

    # -------------------------------------------------------------------- reads

    def current_rating_for(self, *, interaction_id: str, user_id: str) -> RatingRecord | None:
        """This caller's own current rating. Another learner's rating is never returned."""
        try:
            history = self._ratings.for_interaction(interaction_id)
        except PortError as exc:
            log.warning(
                "rating_read_failed", interaction_id=interaction_id, reason_code=exc.reason_code
            )
            raise
        return self._resolve_current(history, user_id)

    def history_for(self, *, interaction_id: str, user_id: str) -> list[RatingRecord]:
        """This caller's full rating history for one interaction, superseded records kept."""
        return [r for r in self._ratings.for_interaction(interaction_id) if r.user_id == user_id]

    # ---------------------------------------------------------------- internals

    @staticmethod
    def _resolve_current(history: list[RatingRecord], user_id: str) -> RatingRecord | None:
        """The most recent rating is authoritative.

        Resolution is by rating time among non-superseded records rather than by trusting
        the supersede marker alone, so the current rating is still correct if a supersede
        marker write failed after the new rating was already persisted.
        """
        mine = [r for r in history if r.user_id == user_id and r.is_current]
        if not mine:
            return None
        return max(mine, key=lambda r: r.rated_at)

    def _build_record(
        self,
        *,
        interaction: InteractionRecord,
        rating: RatingValue,
        comment: str | None,
        rated_at: datetime,
    ) -> RatingRecord:
        cleaned = comment.strip() if comment else None
        return RatingRecord(
            rating_id=new_rating_id(),
            interaction_id=interaction.interaction_id,
            session_id=interaction.session_id,
            user_id=interaction.user_id,
            rating=rating,
            comment=cleaned or None,
            question_text=interaction.question_text,
            response_text=interaction.response_text,
            naric_level=interaction.naric_level,
            session_mode=interaction.session_mode,
            topic_tag=interaction.topic_tag,
            rated_at=rated_at,
            superseded_by=None,
        )

    def _supersede_previous(
        self, *, interaction_id: str, user_id: str, new_rating_id: str
    ) -> str | None:
        """Mark this learner's earlier ratings superseded. Nothing is deleted.

        The new rating is saved *before* this runs, so a failure here can never leave the
        learner with no current rating -- only with a stale marker, which
        :meth:`_resolve_current` already tolerates.
        """
        superseded: str | None = None
        try:
            history = self._ratings.for_interaction(interaction_id)
        except PortError as exc:
            log.warning(
                "rating_supersede_deferred",
                interaction_id=interaction_id,
                reason_code=exc.reason_code,
            )
            return None

        for previous in history:
            if previous.user_id != user_id or previous.rating_id == new_rating_id:
                continue
            if not previous.is_current:
                continue
            try:
                self._ratings.supersede(previous.rating_id, new_rating_id)
            except PortError as exc:
                log.warning(
                    "rating_supersede_deferred",
                    rating_id=previous.rating_id,
                    reason_code=exc.reason_code,
                )
                continue
            superseded = previous.rating_id
            log.info(
                "rating_superseded",
                rating_id=previous.rating_id,
                superseded_by=new_rating_id,
                interaction_id=interaction_id,
                previous_rating=previous.rating.value,
            )
        return superseded

    def _trigger_evaluation(self, topic_tag: str) -> None:
        """Flag evaluation is peripheral to capture and can never fail the capture."""
        if self._on_rating_recorded is None:
            return
        try:
            self._on_rating_recorded(topic_tag)
        except Exception as exc:  # noqa: BLE001 - deliberate isolation boundary
            log.warning(
                "flag_evaluation_failed_after_rating",
                topic_tag=topic_tag,
                error_type=type(exc).__name__,
                retry="next_cycle",
            )
