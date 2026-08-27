"""The composition root.

One place assembles the component. Provider selection happens through
:mod:`uc08.registry`, so integrating a real upstream never reaches this file:
the registry gains one line, the environment gains one value, and this wiring is
unchanged.

There is no DI framework. FastAPI ``Depends`` resolves request-scoped
dependencies from the container built here.
"""

from __future__ import annotations

from dataclasses import dataclass

from uc08.adapters.clock.clocks import SystemClock
from uc08.adapters.identity.header import HeaderCurrentUserProvider
from uc08.adapters.persistence import jsonfile, memory
from uc08.adapters.sinks.local import LoggingEngineeringAlertSink, RecordingNotificationSink
from uc08.application.badge_service import BadgeService
from uc08.application.streak_persistence import StreakWriter
from uc08.application.streak_service import StreakService
from uc08.application.weekly_summary_service import WeeklySummaryService
from uc08.config import PersistenceBackend, Settings, load_settings
from uc08.logging_setup import configure_logging, get_logger
from uc08.ports.clock import Clock
from uc08.ports.identity import CurrentUserProvider
from uc08.ports.repositories import (
    BadgeRepository,
    FreezeOfferRepository,
    ProcessedInteractionStore,
    StreakRepository,
    WeeklySummaryRepository,
)
from uc08.ports.sinks import EngineeringAlertSink, NotificationSink
from uc08.ports.upstream import ActivityProvider, GapReportProvider
from uc08.registry import build_provider

_log = get_logger(__name__)


@dataclass
class Container:
    settings: Settings
    clock: Clock
    activity: ActivityProvider
    gap_report: GapReportProvider
    streaks: StreakRepository
    badges: BadgeRepository
    summaries: WeeklySummaryRepository
    freeze_offers: FreezeOfferRepository
    processed: ProcessedInteractionStore
    notifications: NotificationSink
    alerts: EngineeringAlertSink
    identity: CurrentUserProvider
    streak_service: StreakService
    badge_service: BadgeService
    weekly_summary_service: WeeklySummaryService


def build_container(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    activity: ActivityProvider | None = None,
    gap_report: GapReportProvider | None = None,
    streaks: StreakRepository | None = None,
    badges: BadgeRepository | None = None,
    summaries: WeeklySummaryRepository | None = None,
    freeze_offers: FreezeOfferRepository | None = None,
    processed: ProcessedInteractionStore | None = None,
    notifications: NotificationSink | None = None,
    alerts: EngineeringAlertSink | None = None,
    identity: CurrentUserProvider | None = None,
) -> Container:
    """Assemble the component.

    Every keyword is an override for a test or an embedding host. Left alone,
    each one is built from configuration -- upstream providers through the
    registry, which raises loudly at this point if a configured provider name
    has no registered implementation. There is no fallback to a mock.
    """
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    clock = clock or SystemClock()
    activity = activity or build_provider(
        "activity", settings.activity_provider, clock, timeout_seconds=settings.provider_timeout_seconds
    )
    gap_report = gap_report or build_provider(
        "gap_report", settings.gap_report_provider, clock, timeout_seconds=settings.provider_timeout_seconds
    )

    repositories = _build_persistence(settings)
    streaks = streaks or repositories["streaks"]
    badges = badges or repositories["badges"]
    summaries = summaries or repositories["summaries"]
    freeze_offers = freeze_offers or repositories["freeze_offers"]
    processed = processed or repositories["processed"]

    notifications = notifications or RecordingNotificationSink()
    alerts = alerts or LoggingEngineeringAlertSink()
    identity = identity or HeaderCurrentUserProvider(settings.dev_identity_header)

    badge_service = BadgeService(
        activity=activity,
        badges=badges,
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
    weekly_summary_service = WeeklySummaryService(
        clock=clock,
        activity=activity,
        gap_report=gap_report,
        streaks=streaks,
        summaries=summaries,
        notifications=notifications,
        summary_day=settings.weekly_summary_day,
    )

    _log.info(
        "container_built",
        extra={
            "activity_provider": settings.activity_provider,
            "gap_report_provider": settings.gap_report_provider,
            "persistence": settings.persistence.value,
            "streak_window_hours": settings.streak_window_hours,
            "badge_milestones": list(settings.badge_milestones),
            "freeze_min_streak_days": settings.freeze_min_streak_days,
            "freeze_offer_expiry_hours": settings.freeze_offer_expiry_hours,
            "weekly_summary_day": settings.weekly_summary_day.value,
            "allow_dev_session_minting": settings.allow_dev_session_minting,
        },
    )

    return Container(
        settings=settings,
        clock=clock,
        activity=activity,
        gap_report=gap_report,
        streaks=streaks,
        badges=badges,
        summaries=summaries,
        freeze_offers=freeze_offers,
        processed=processed,
        notifications=notifications,
        alerts=alerts,
        identity=identity,
        streak_service=streak_service,
        badge_service=badge_service,
        weekly_summary_service=weekly_summary_service,
    )


def _build_persistence(settings: Settings) -> dict[str, object]:
    if settings.persistence is PersistenceBackend.JSONFILE:
        directory = settings.persistence_dir
        return {
            "streaks": jsonfile.JsonFileStreakRepository(directory),
            "badges": jsonfile.JsonFileBadgeRepository(directory),
            "summaries": jsonfile.JsonFileWeeklySummaryRepository(directory),
            "freeze_offers": jsonfile.JsonFileFreezeOfferRepository(directory),
            "processed": jsonfile.JsonFileProcessedInteractionStore(directory),
        }
    return {
        "streaks": memory.InMemoryStreakRepository(),
        "badges": memory.InMemoryBadgeRepository(),
        "summaries": memory.InMemoryWeeklySummaryRepository(),
        "freeze_offers": memory.InMemoryFreezeOfferRepository(),
        "processed": memory.InMemoryProcessedInteractionStore(),
    }
