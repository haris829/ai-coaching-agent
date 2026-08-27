"""Shared fixtures.

No test sleeps, and no test reads the machine clock. Time is a
:class:`~uc08.adapters.clock.clocks.FixedClock` that only moves when a test
advances it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from uc08.adapters.clock.clocks import FixedClock
from uc08.adapters.mock.activity import MockActivityProvider
from uc08.adapters.mock.gap_report import GapReportPlan, MockGapReportProvider
from uc08.adapters.mock.ledger import ActivityLedger
from uc08.adapters.persistence.memory import (
    InMemoryBadgeRepository,
    InMemoryFreezeOfferRepository,
    InMemoryProcessedInteractionStore,
    InMemoryStreakRepository,
    InMemoryWeeklySummaryRepository,
)
from uc08.adapters.sinks.local import LoggingEngineeringAlertSink, RecordingNotificationSink
from uc08.application.badge_service import BadgeService
from uc08.application.session import ResolvedSession
from uc08.application.streak_persistence import StreakWriter
from uc08.application.streak_service import StreakService
from uc08.application.weekly_summary_service import WeeklySummaryService
from uc08.config import Settings, load_settings
from uc08.domain.enums import SessionIdSource
from uc08.logging_setup import configure_logging
from uc08.ports.repositories import StreakRepository

#: A Tuesday, 12:00 UTC. Mid-week and mid-day, so a 24-hour window straddles two
#: calendar days in both directions and the window-vs-calendar-day distinction is
#: visible rather than accidental.
ANCHOR = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)

#: A Monday, 09:00 UTC. The weekly summary day.
MONDAY = datetime(2026, 3, 16, 9, 0, 0, tzinfo=timezone.utc)

USER = "learner-7781"
OTHER_USER = "learner-9902"

configure_logging("WARNING")


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(ANCHOR)


@pytest.fixture
def monday_clock() -> FixedClock:
    return FixedClock(MONDAY)


@pytest.fixture
def ledger() -> ActivityLedger:
    return ActivityLedger()


@pytest.fixture
def gap_plan() -> GapReportPlan:
    return GapReportPlan()


@dataclass
class Harness:
    """A wired UC-08 with every seam exposed for a test to drive."""

    settings: Settings
    clock: FixedClock
    ledger: ActivityLedger
    gap_plan: GapReportPlan
    streaks: StreakRepository
    badges_repo: InMemoryBadgeRepository
    summaries: InMemoryWeeklySummaryRepository
    freeze_offers: InMemoryFreezeOfferRepository
    processed: InMemoryProcessedInteractionStore
    notifications: RecordingNotificationSink
    alerts: LoggingEngineeringAlertSink
    streak_service: StreakService
    badge_service: BadgeService
    summary_service: WeeklySummaryService

    def record(self, interaction_id: str, *, user_id: str = USER, session_id: str = "sess-1"):
        """Record a coaching interaction that the activity read model also sees.

        This mirrors production: the interaction exists upstream, and UC-08 is
        told about it.
        """
        self.ledger.add_interaction(user_id, self.clock.now(), interaction_id, topic="professional-conduct")
        return self.streak_service.record_activity(
            user_id=user_id,
            interaction_id=interaction_id,
            session=ResolvedSession(session_id, SessionIdSource.RECEIVED),
        )

    def record_without_upstream_echo(self, interaction_id: str, *, user_id: str = USER):
        """Record an interaction the activity read model has not yet seen."""
        return self.streak_service.record_activity(
            user_id=user_id,
            interaction_id=interaction_id,
            session=ResolvedSession("sess-1", SessionIdSource.RECEIVED),
        )


def build_harness(
    clock: FixedClock,
    *,
    ledger: ActivityLedger | None = None,
    gap_plan: GapReportPlan | None = None,
    streaks: StreakRepository | None = None,
    notifications: RecordingNotificationSink | None = None,
    settings: Settings | None = None,
) -> Harness:
    settings = settings or load_settings(
        ACTIVITY_PROVIDER="mock",
        GAP_REPORT_PROVIDER="mock",
        STREAK_WINDOW_HOURS=24,
        BADGE_MILESTONES="10,50,100",
        FREEZE_MIN_STREAK_DAYS=7,
        FREEZE_PER_CALENDAR_MONTH=1,
        WEEKLY_SUMMARY_DAY="monday",
    )
    ledger = ledger if ledger is not None else ActivityLedger()
    gap_plan = gap_plan if gap_plan is not None else GapReportPlan()
    activity = MockActivityProvider(clock, ledger, timeout_seconds=settings.provider_timeout_seconds)
    gap_report = MockGapReportProvider(clock, gap_plan, timeout_seconds=settings.provider_timeout_seconds)

    streaks = streaks if streaks is not None else InMemoryStreakRepository()
    badges_repo = InMemoryBadgeRepository()
    summaries = InMemoryWeeklySummaryRepository()
    freeze_offers = InMemoryFreezeOfferRepository()
    processed = InMemoryProcessedInteractionStore()
    notifications = notifications if notifications is not None else RecordingNotificationSink()
    alerts = LoggingEngineeringAlertSink()

    badge_service = BadgeService(
        activity=activity,
        badges=badges_repo,
        notifications=notifications,
        milestones=settings.badge_milestones,
    )
    streak_service = StreakService(
        clock=clock,
        activity=activity,
        streaks=streaks,
        freeze_offers=freeze_offers,
        processed=processed,
        writer=StreakWriter(repository=streaks, alerts=alerts, clock=clock),
        badges=badge_service,
        window_hours=settings.streak_window_hours,
        freeze_min_streak_days=settings.freeze_min_streak_days,
        freeze_offer_expiry_hours=settings.freeze_offer_expiry_hours,
    )
    summary_service = WeeklySummaryService(
        clock=clock,
        activity=activity,
        gap_report=gap_report,
        streaks=streaks,
        summaries=summaries,
        notifications=notifications,
        summary_day=settings.weekly_summary_day,
    )
    return Harness(
        settings=settings,
        clock=clock,
        ledger=ledger,
        gap_plan=gap_plan,
        streaks=streaks,
        badges_repo=badges_repo,
        summaries=summaries,
        freeze_offers=freeze_offers,
        processed=processed,
        notifications=notifications,
        alerts=alerts,
        streak_service=streak_service,
        badge_service=badge_service,
        summary_service=summary_service,
    )


@pytest.fixture
def harness(clock: FixedClock, ledger: ActivityLedger, gap_plan: GapReportPlan) -> Harness:
    return build_harness(clock, ledger=ledger, gap_plan=gap_plan)


@pytest.fixture
def monday_harness(monday_clock: FixedClock, ledger: ActivityLedger, gap_plan: GapReportPlan) -> Harness:
    return build_harness(monday_clock, ledger=ledger, gap_plan=gap_plan)
