"""Outbound sinks.

UC-08 emits events for a caller to render and incidents for engineering to act
on. It renders nothing itself: there is no notification UI in this component.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from uc08.domain.models import BadgeAwardedEvent, StreakWriteIncident, WeeklySummaryEvent


class NotificationSink(ABC):
    """Delivery of learner-facing events.

    Failures raise :class:`~uc08.domain.errors.NotificationSendFailed`. A
    notification failure never blocks coaching and never changes a streak.
    """

    @abstractmethod
    def badge_awarded(self, event: BadgeAwardedEvent) -> None:
        """Emit the in-chat badge notification for a caller to render."""

    @abstractmethod
    def weekly_summary(self, event: WeeklySummaryEvent) -> None:
        """Deliver a generated weekly summary.

        The summary record is written and logged before this is called, so a
        failure here loses delivery, never the record.
        """


class EngineeringAlertSink(ABC):
    """Operational escalation. Not learner-facing."""

    @abstractmethod
    def streak_write_failed(self, incident: StreakWriteIncident) -> None:
        """Alert engineering that a streak write did not commit after a retry.

        The incident carries the preserved count. Implementations must not
        raise; a failing alert sink must not turn a persistence problem into a
        request failure.
        """
