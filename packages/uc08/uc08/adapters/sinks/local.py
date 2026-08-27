"""Local sink adapters: log the event, keep it retrievable, render nothing.

UC-08 emits notification events for a caller to render. There is no
notification UI here, and there is no invented delivery endpoint: replacing
these with the platform bus is an adapter swap.
"""

from __future__ import annotations

from uc08.domain.errors import NotificationSendFailed
from uc08.domain.models import BadgeAwardedEvent, StreakWriteIncident, WeeklySummaryEvent
from uc08.logging_setup import get_logger
from uc08.ports.sinks import EngineeringAlertSink, NotificationSink

_log = get_logger(__name__)


class RecordingNotificationSink(NotificationSink):
    """Log each event and retain it in order for a caller (or a test) to read."""

    def __init__(self) -> None:
        self.badge_events: list[BadgeAwardedEvent] = []
        self.summary_events: list[WeeklySummaryEvent] = []

    def badge_awarded(self, event: BadgeAwardedEvent) -> None:
        self.badge_events.append(event)
        _log.info(
            "badge_awarded_notification_emitted",
            extra={
                "event_type": event.event_type,
                "user_id": event.user_id,
                "milestone": event.milestone,
                "badge_id": event.badge_id,
            },
        )

    def weekly_summary(self, event: WeeklySummaryEvent) -> None:
        self.summary_events.append(event)
        _log.info(
            "weekly_summary_notification_emitted",
            extra={
                "event_type": event.event_type,
                "user_id": event.user_id,
                "week": event.week,
                "summary_id": event.summary_id,
            },
        )


class FailingWeeklySummarySink(RecordingNotificationSink):
    """Deterministic delivery failure for the summary channel.

    Badge notifications still succeed: a broken summary channel must not take
    the badge channel with it. ``fail_sends`` failures, then normal behaviour.
    """

    def __init__(self, *, fail_sends: int = 1) -> None:
        super().__init__()
        self._remaining_failures = fail_sends
        self.send_attempts = 0

    def weekly_summary(self, event: WeeklySummaryEvent) -> None:
        self.send_attempts += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise NotificationSendFailed(f"injected send failure (attempt {self.send_attempts})")
        super().weekly_summary(event)


class LoggingEngineeringAlertSink(EngineeringAlertSink):
    """Escalate to engineering via a structured log record, and retain it.

    Never raises: a failing alert path must not turn a persistence problem into
    a failed coaching request.
    """

    def __init__(self) -> None:
        self.incidents: list[StreakWriteIncident] = []

    def streak_write_failed(self, incident: StreakWriteIncident) -> None:
        self.incidents.append(incident)
        _log.error(
            "streak_write_failed_alert",
            extra={
                "incident_id": incident.incident_id,
                "user_id": incident.user_id,
                "attempts": incident.attempts,
                "preserved_streak_days": incident.preserved_streak_days,
                "preserved_longest_streak_days": incident.preserved_longest_streak_days,
                "intended_streak_days": incident.intended_streak_days,
                "error_type": incident.error_type,
                "alert_severity": "page_engineering",
            },
        )
