"""Milestone badges.

Rules implemented here:

* Award when the total question count crosses a configured threshold.
* Award **exactly once**. Badge ids are derived from ``(user_id, milestone)``,
  so a duplicate is not merely avoided by a check -- it is not representable.
* A count that jumps past several thresholds awards **every** one crossed, in
  ascending order, not only the highest.
* Badges are permanent. There is no removal method on the repository port and
  no function in this module that deletes, revokes or expires one.
* Each award emits a notification event for a caller to render. UC-08 renders
  nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from uc08.application.degradation import status_for_provider_error
from uc08.domain.enums import SourceStatus
from uc08.domain.errors import NotificationSendFailed, ProviderError, RepositoryError
from uc08.domain.models import Badge, BadgeAwardedEvent
from uc08.logging_setup import get_logger
from uc08.ports.repositories import BadgeRepository
from uc08.ports.sinks import NotificationSink
from uc08.ports.upstream import ActivityProvider

_log = get_logger(__name__)


def badge_id_for(user_id: str, milestone: int) -> str:
    """Deterministic, so the same milestone can only ever be one badge (A-21)."""
    return f"badge-{milestone}-{user_id}"


def badge_event_id_for(user_id: str, milestone: int) -> str:
    return f"evt-badge-{milestone}-{user_id}"


@dataclass(frozen=True)
class BadgeEvaluation:
    awarded: tuple[Badge, ...]
    events: tuple[BadgeAwardedEvent, ...]
    question_count: int | None
    question_count_status: SourceStatus


class BadgeService:
    def __init__(
        self,
        *,
        activity: ActivityProvider,
        badges: BadgeRepository,
        notifications: NotificationSink,
        milestones: tuple[int, ...],
    ) -> None:
        self._activity = activity
        self._badges = badges
        self._notifications = notifications
        self._milestones = tuple(sorted(milestones))

    def held(self, user_id: str) -> tuple[Badge, ...]:
        return self._badges.get_all(user_id)

    def evaluate(self, user_id: str, now: datetime) -> BadgeEvaluation:
        """Award every milestone the current count has crossed.

        A failure reading the count, writing a badge or sending a notification
        is logged and reported; it never propagates. Badges are an incentive
        feature, and losing one temporarily must not fail a coaching
        interaction or touch the streak.
        """
        try:
            read = self._activity.question_count(user_id)
        except ProviderError as exc:
            status = status_for_provider_error(exc)
            _log.warning(
                "badge_question_count_unavailable",
                extra={"user_id": user_id, "port": exc.port, "question_count_status": status.value},
            )
            return BadgeEvaluation((), (), None, status)

        if read.status is SourceStatus.EMPTY:
            return BadgeEvaluation((), (), read.count, read.status)

        already_held = {badge.milestone for badge in self._badges.get_all(user_id)}
        crossed = [
            milestone
            for milestone in self._milestones
            if read.count >= milestone and milestone not in already_held
        ]

        awarded: list[Badge] = []
        events: list[BadgeAwardedEvent] = []
        for milestone in crossed:
            badge = Badge(
                badge_id=badge_id_for(user_id, milestone),
                user_id=user_id,
                milestone=milestone,
                awarded_at=now,
                question_count_at_award=read.count,
            )
            try:
                self._badges.award(badge)
            except RepositoryError:
                _log.error(
                    "badge_award_write_failed",
                    extra={"user_id": user_id, "milestone": milestone},
                    exc_info=True,
                )
                continue
            awarded.append(badge)
            _log.info(
                "badge_awarded",
                extra={
                    "user_id": user_id,
                    "milestone": milestone,
                    "badge_id": badge.badge_id,
                    "question_count_at_award": read.count,
                },
            )
            event = BadgeAwardedEvent(
                event_id=badge_event_id_for(user_id, milestone),
                user_id=user_id,
                badge_id=badge.badge_id,
                milestone=milestone,
                question_count_at_award=read.count,
                awarded_at=badge.awarded_at,
                occurred_at=now,
            )
            events.append(event)
            try:
                self._notifications.badge_awarded(event)
            except NotificationSendFailed:
                # The badge is awarded and permanent. Only the notification was
                # lost, and the event is still returned for the caller to render.
                _log.warning(
                    "badge_notification_send_failed",
                    extra={"user_id": user_id, "milestone": milestone, "badge_id": badge.badge_id},
                )

        return BadgeEvaluation(tuple(awarded), tuple(events), read.count, read.status)
