"""Streak tracking, freeze offers, and the read side of both.

Everything time-related goes through the injected clock. Nothing here calls the
system clock, and nothing here constructs a reset: the reset comes from
:func:`uc08.domain.streak_rules.apply_reset`, which requires evidence produced
only by :func:`uc08.domain.streak_rules.decide`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from uc08.application.badge_service import BadgeService
from uc08.application.degradation import status_for_provider_error
from uc08.application.session import ResolvedSession
from uc08.application.streak_persistence import PersistResult, StreakWriter
from uc08.domain import streak_rules
from uc08.domain.enums import FreezeOfferStatus, PersistenceOutcome, SourceStatus, StreakOutcome
from uc08.domain.errors import FreezeNotAvailable, ProviderError, RepositoryError
from uc08.domain.models import Badge, FreezeOffer, RecordActivityResult, StreakRecord
from uc08.logging_setup import get_logger
from uc08.ports.clock import Clock
from uc08.ports.repositories import FreezeOfferRepository, ProcessedInteractionStore, StreakRepository
from uc08.ports.upstream import ActivityProvider

_log = get_logger(__name__)


@dataclass(frozen=True)
class StreakState:
    """Read model for ``GET /api/v1/streaks``."""

    streak: StreakRecord
    open_freeze_offer: FreezeOffer | None
    badges: tuple[Badge, ...]


@dataclass(frozen=True)
class _Continuity:
    """The evidence about prior activity, and where it came from."""

    prior_interactions_in_window: int
    activity_status: SourceStatus
    source_degraded: bool


class StreakService:
    def __init__(
        self,
        *,
        clock: Clock,
        activity: ActivityProvider,
        streaks: StreakRepository,
        freeze_offers: FreezeOfferRepository,
        processed: ProcessedInteractionStore,
        writer: StreakWriter,
        badges: BadgeService,
        window_hours: int,
        freeze_min_streak_days: int,
        freeze_offer_expiry_hours: int,
    ) -> None:
        self._clock = clock
        self._activity = activity
        self._streaks = streaks
        self._freeze_offers = freeze_offers
        self._processed = processed
        self._writer = writer
        self._badges = badges
        self._window_hours = window_hours
        self._freeze_min_streak_days = freeze_min_streak_days
        self._freeze_offer_expiry_hours = freeze_offer_expiry_hours

    # ----------------------------------------------------------------------
    # Reads
    # ----------------------------------------------------------------------
    def get_state(self, user_id: str) -> StreakState:
        now = self._clock.now()
        streak = self._streaks.get(user_id) or self._empty_record(user_id, now)
        return StreakState(
            streak=streak,
            open_freeze_offer=self._open_offer(user_id, now),
            badges=self._badges.held(user_id),
        )

    # ----------------------------------------------------------------------
    # Record activity
    # ----------------------------------------------------------------------
    def record_activity(
        self,
        *,
        user_id: str,
        interaction_id: str,
        session: ResolvedSession,
    ) -> RecordActivityResult:
        now = self._clock.now()
        existing = self._streaks.get(user_id)

        if self._processed.was_processed(user_id, interaction_id):
            # Replaying an interaction changes nothing at all.
            _log.info(
                "record_activity_idempotent_replay",
                extra={"user_id": user_id, "interaction_id": interaction_id},
            )
            return RecordActivityResult(
                streak=existing or self._empty_record(user_id, now),
                outcome=StreakOutcome.IDEMPOTENT_REPLAY,
                persistence_outcome=PersistenceOutcome.SAVED,
                idempotent_replay=True,
                session_id=session.session_id,
                session_id_source=session.source,
                freeze_offer=self._open_offer(user_id, now),
            )

        continuity = self._read_continuity(user_id, interaction_id, existing, now)

        intended, outcome, offer = self._next_record(user_id, existing, now, continuity)
        persisted = self._writer.persist(intended, last_known=existing)

        if persisted.committed:
            self._mark_processed(user_id, interaction_id)
            if offer is not None:
                self._save_offer(offer)
        else:
            # Nothing was committed. The last known record stands, the
            # interaction is not marked processed so a caller retry can still
            # apply it, and no freeze offer is created off the back of a state
            # that was never written.
            offer = None

        _log.info(
            "activity_recorded",
            extra={
                "user_id": user_id,
                "interaction_id": interaction_id,
                "session_id_source": session.source.value,
                "outcome": outcome.value,
                "persistence_outcome": persisted.outcome.value,
                "current_streak_days": persisted.record.current_streak_days,
                "longest_streak_days": persisted.record.longest_streak_days,
                "activity_status": continuity.activity_status.value,
                "window_hours": self._window_hours,
                "freeze_offered": offer is not None,
            },
        )

        evaluation = self._badges.evaluate(user_id, now)

        return RecordActivityResult(
            streak=persisted.record,
            outcome=outcome,
            persistence_outcome=persisted.outcome,
            idempotent_replay=False,
            session_id=session.session_id,
            session_id_source=session.source,
            awarded_badges=evaluation.awarded,
            badge_events=evaluation.events,
            freeze_offer=offer or self._open_offer(user_id, now),
            question_count=evaluation.question_count,
            question_count_status=evaluation.question_count_status,
            activity_status=continuity.activity_status,
        )

    # ----------------------------------------------------------------------
    # Freeze
    # ----------------------------------------------------------------------
    def accept_freeze(self, user_id: str) -> StreakState:
        now = self._clock.now()
        offer = self._open_offer(user_id, now)
        if offer is None:
            raise FreezeNotAvailable("no freeze offer is open for this account")

        streak = self._streaks.get(user_id)
        if streak is None:
            raise FreezeNotAvailable("no streak record to restore")
        if not streak_rules.freeze_available_at(now, streak.freeze_used_at):
            raise FreezeNotAvailable("a freeze has already been used this calendar month")
        if streak.streak_started_at != offer.offered_at:
            # The streak has moved on since the offer was made; restoring would
            # credit days the learner did not earn.
            raise FreezeNotAvailable("the offer no longer matches the current streak")

        restored = streak_rules.apply_freeze_acceptance(streak, offer, now)
        persisted = self._writer.persist(restored, last_known=streak)
        if not persisted.committed:
            raise FreezeNotAvailable("the freeze could not be recorded; the streak is unchanged")

        self._save_offer(
            offer.model_copy(update={"status": FreezeOfferStatus.ACCEPTED, "answered_at": now})
        )
        _log.info(
            "freeze_accepted",
            extra={
                "user_id": user_id,
                "offer_id": offer.offer_id,
                "restored_streak_days": restored.current_streak_days,
                "preserved_streak_days": offer.preserved_streak_days,
            },
        )
        return StreakState(
            streak=persisted.record,
            open_freeze_offer=None,
            badges=self._badges.held(user_id),
        )

    def decline_freeze(self, user_id: str) -> StreakState:
        """Record a declined offer.

        The streak already reset when the missed day was determined, so
        declining changes no count -- it closes the offer. Not exposed as an
        endpoint: the platform API surface for UC-08 is fixed and has no decline
        route. It is listed as an extension point in ``docs/SHARED_CONTRACT.md``.
        """
        now = self._clock.now()
        offer = self._open_offer(user_id, now)
        if offer is None:
            raise FreezeNotAvailable("no freeze offer is open for this account")
        self._save_offer(
            offer.model_copy(update={"status": FreezeOfferStatus.DECLINED, "answered_at": now})
        )
        _log.info("freeze_declined", extra={"user_id": user_id, "offer_id": offer.offer_id})
        return self.get_state(user_id)

    # ----------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------
    def _read_continuity(
        self,
        user_id: str,
        interaction_id: str,
        existing: StreakRecord | None,
        now: datetime,
    ) -> _Continuity:
        """Count prior qualifying interactions in the trailing window.

        Continuity is decided from the activity read model, with the persisted
        record as the degraded-mode fallback (A-04).

        The interaction being recorded is excluded, both by id and by
        timestamp (A-09): an activity read model that already shows it must not make
        every interaction look like continuous activity.
        """
        window_start = streak_rules.window_start_for(now, self._window_hours)
        try:
            read = self._activity.interactions_in_window(user_id, window_start)
        except ProviderError as exc:
            status = status_for_provider_error(exc)
            _log.warning(
                "activity_read_degraded",
                extra={
                    "user_id": user_id,
                    "port": exc.port,
                    "activity_status": status.value,
                    "fallback": "persisted_streak_record",
                },
            )
            return _Continuity(
                prior_interactions_in_window=self._prior_from_record(existing, now),
                activity_status=status,
                source_degraded=True,
            )

        prior = [
            interaction
            for interaction in read.interactions
            if interaction.interaction_id != interaction_id and interaction.occurred_at < now
        ]
        return _Continuity(
            prior_interactions_in_window=len(prior),
            activity_status=read.status,
            source_degraded=False,
        )

    def _prior_from_record(self, existing: StreakRecord | None, now: datetime) -> int:
        """Degraded-mode continuity, taken from what this component itself last
        counted."""
        if existing is None or existing.last_activity_at is None:
            return 0
        return 1 if now - existing.last_activity_at <= timedelta(hours=self._window_hours) else 0

    def _next_record(
        self,
        user_id: str,
        existing: StreakRecord | None,
        now: datetime,
        continuity: _Continuity,
    ) -> tuple[StreakRecord, StreakOutcome, FreezeOffer | None]:
        decision = streak_rules.decide(
            user_id=user_id,
            streak=existing,
            now=now,
            prior_interactions_in_window=continuity.prior_interactions_in_window,
            window_hours=self._window_hours,
        )

        if decision.outcome is StreakOutcome.STARTED:
            return streak_rules.apply_start(user_id=user_id, now=now), StreakOutcome.STARTED, None

        assert existing is not None  # STARTED is the only branch with no record

        if decision.outcome is StreakOutcome.UNCHANGED_SAME_DAY:
            return streak_rules.apply_same_day(existing, now), StreakOutcome.UNCHANGED_SAME_DAY, None

        if decision.outcome is StreakOutcome.INCREMENTED:
            return streak_rules.apply_increment(existing, now), StreakOutcome.INCREMENTED, None

        # A reset was determined. If the activity read model could not be
        # consulted, the determination is not trustworthy, so the count is
        # preserved instead. This is the only place that distinction is made,
        # and it is made before the reset builder is reached.
        if continuity.source_degraded:
            _log.warning(
                "streak_preserved_source_degraded",
                extra={
                    "user_id": user_id,
                    "current_streak_days": existing.current_streak_days,
                    "activity_status": continuity.activity_status.value,
                    "reset_applied": False,
                },
            )
            return (
                streak_rules.apply_preserve_on_degraded_source(existing, now),
                StreakOutcome.UNCHANGED_SOURCE_DEGRADED,
                None,
            )

        evidence = decision.inactivity_evidence
        assert evidence is not None  # decide() attaches evidence to every reset
        offer = self._build_offer_if_eligible(existing, now)
        return streak_rules.apply_reset(existing, now, evidence), StreakOutcome.RESET, offer

    def _build_offer_if_eligible(self, existing: StreakRecord, now: datetime) -> FreezeOffer | None:
        """Offer a freeze on a missed day, if the learner qualifies.

        Failure here is swallowed: a freeze is an incentive, and its failure must
        never block coaching or alter the streak.
        """
        try:
            if not streak_rules.eligible_for_freeze_offer(
                existing, now=now, min_streak_days=self._freeze_min_streak_days
            ):
                return None
            return FreezeOffer(
                offer_id=f"fo-{existing.user_id}-{now.strftime('%Y%m%dT%H%M%S%f')}Z",
                user_id=existing.user_id,
                status=FreezeOfferStatus.OFFERED,
                offered_at=now,
                expires_at=now + timedelta(hours=self._freeze_offer_expiry_hours),
                preserved_streak_days=existing.current_streak_days,
                preserved_streak_started_at=existing.streak_started_at,
            )
        except Exception:
            _log.error(
                "freeze_offer_build_failed",
                extra={"user_id": existing.user_id, "coaching_blocked": False},
                exc_info=True,
            )
            return None

    def _save_offer(self, offer: FreezeOffer) -> None:
        try:
            self._freeze_offers.save(offer)
        except RepositoryError:
            _log.error(
                "freeze_offer_write_failed",
                extra={"user_id": offer.user_id, "offer_id": offer.offer_id, "coaching_blocked": False},
                exc_info=True,
            )

    def _open_offer(self, user_id: str, now: datetime) -> FreezeOffer | None:
        """The offer a learner may still accept, expiring it lazily if due.

        An offer that is never answered stops being acceptable at
        ``expires_at``; it does not preserve a streak indefinitely, and because
        the reset was already applied when the offer was made, an expiry needs
        no compensating write to the streak.
        """
        try:
            offer = self._freeze_offers.get_latest(user_id)
        except RepositoryError:
            _log.error("freeze_offer_read_failed", extra={"user_id": user_id}, exc_info=True)
            return None
        if offer is None or offer.status is not FreezeOfferStatus.OFFERED:
            return None
        if offer.is_open_at(now):
            return offer
        self._save_offer(offer.model_copy(update={"status": FreezeOfferStatus.EXPIRED}))
        _log.info(
            "freeze_offer_expired",
            extra={"user_id": user_id, "offer_id": offer.offer_id, "streak_preserved": False},
        )
        return None

    def _mark_processed(self, user_id: str, interaction_id: str) -> None:
        try:
            self._processed.mark_processed(user_id, interaction_id)
        except RepositoryError:
            # Worst case a replay is counted once more on the same day, which
            # the once-per-day rule already absorbs.
            _log.warning(
                "processed_interaction_write_failed",
                extra={"user_id": user_id, "interaction_id": interaction_id},
            )

    @staticmethod
    def _empty_record(user_id: str, now: datetime) -> StreakRecord:
        """A zero-valued view for an account with no record yet (A-26).

        Never persisted, and never a reset of anything: there is nothing to
        reset.
        """
        return StreakRecord(
            user_id=user_id,
            current_streak_days=0,
            longest_streak_days=0,
            last_activity_at=None,
            streak_started_at=None,
            freeze_available=True,
            freeze_used_at=None,
            updated_at=now,
        )


__all__ = ["StreakService", "StreakState", "PersistResult"]
