"""Proof that the swap is real.

The **unmodified** service is run against every registered adapter family and
must produce identical results. One family is deliberately foreign: different
field names, different nesting (``data.timeline.entries``), different value
representations (epoch milliseconds for time, a string for the question count,
prose for the NARIC level, ``"64%"`` for completion).

Nothing in ``uc08/domain``, ``uc08/application`` or ``uc08/api`` knows either
family exists. If the outputs match, replaceability is demonstrated rather than
asserted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from uc08.adapters.clock.clocks import FixedClock
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
from uc08.config import Weekday, load_settings
from uc08.domain.enums import SessionIdSource
from uc08.domain.models import StreakRecord
from uc08.ports.conformance import (
    BEHAVIOURAL_ACTIVITY_SCENARIOS,
    BEHAVIOURAL_GAP_REPORT_SCENARIOS,
    CONFORMANCE_USER_ID,
)
from uc08.registry import registered_classes

#: Where the scenario fixture data is positioned (a Tuesday, ISO week 11).
SCENARIO_NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
#: The Monday after that week, so the scenario activity falls in "last week".
SUMMARY_MONDAY = datetime(2026, 3, 16, 9, 0, tzinfo=timezone.utc)

USER = CONFORMANCE_USER_ID
SETTINGS = load_settings()


def _families() -> list[str]:
    """Families that declare the full behavioural scenario set for both ports."""
    activity = registered_classes("activity")
    gap = registered_classes("gap_report")
    names = []
    for name in sorted(set(activity) & set(gap)):
        activity_scenarios = set(activity[name].conformance_scenarios())
        gap_scenarios = set(gap[name].conformance_scenarios())
        if set(BEHAVIOURAL_ACTIVITY_SCENARIOS) <= activity_scenarios and set(
            BEHAVIOURAL_GAP_REPORT_SCENARIOS
        ) <= gap_scenarios:
            names.append(name)
    return names


def _build_activity(family: str, scenario: str):
    builder = registered_classes("activity")[family].conformance_scenarios()[scenario]
    return builder(FixedClock(SCENARIO_NOW))


def _build_gap(family: str, scenario: str):
    builder = registered_classes("gap_report")[family].conformance_scenarios()[scenario]
    return builder(FixedClock(SCENARIO_NOW))


def _streak_service(activity, clock, streaks, badges_repo, notifications):
    badge_service = BadgeService(
        activity=activity,
        badges=badges_repo,
        notifications=notifications,
        milestones=SETTINGS.badge_milestones,
    )
    return StreakService(
        clock=clock,
        activity=activity,
        streaks=streaks,
        freeze_offers=InMemoryFreezeOfferRepository(),
        processed=InMemoryProcessedInteractionStore(),
        writer=StreakWriter(repository=streaks, alerts=LoggingEngineeringAlertSink(), clock=clock),
        badges=badge_service,
        window_hours=SETTINGS.streak_window_hours,
        freeze_min_streak_days=SETTINGS.freeze_min_streak_days,
        freeze_offer_expiry_hours=SETTINGS.freeze_offer_expiry_hours,
    )


def _record_activity_outcome(family: str, scenario: str, *, seeded_streak: int = 4) -> dict:
    """Run one record-activity through the service, unmodified."""
    activity = _build_activity(family, scenario)
    clock = FixedClock(SCENARIO_NOW)
    streaks = InMemoryStreakRepository()
    notifications = RecordingNotificationSink()

    last_activity_at = activity.last_activity_at(USER) if scenario != "no_activity" else None
    if last_activity_at is not None:
        streaks.save(
            StreakRecord(
                user_id=USER,
                current_streak_days=seeded_streak,
                longest_streak_days=seeded_streak,
                last_activity_at=last_activity_at,
                streak_started_at=last_activity_at - timedelta(days=seeded_streak - 1),
                freeze_available=True,
                freeze_used_at=None,
                updated_at=last_activity_at,
            )
        )

    service = _streak_service(activity, clock, streaks, InMemoryBadgeRepository(), notifications)
    result = service.record_activity(
        user_id=USER,
        interaction_id="probe-interaction",
        session=ResolvedSession("sess-probe", SessionIdSource.RECEIVED),
    )
    return {
        "outcome": result.outcome.value,
        "current_streak_days": result.streak.current_streak_days,
        "longest_streak_days": result.streak.longest_streak_days,
        "persistence_outcome": result.persistence_outcome.value,
        "activity_status": result.activity_status.value,
        "question_count": result.question_count,
        "question_count_status": result.question_count_status.value,
        "awarded_milestones": [badge.milestone for badge in result.awarded_badges],
        "notification_milestones": [event.milestone for event in notifications.badge_events],
        "freeze_offered": result.freeze_offer is not None,
        "stored_streak_days": streaks.get(USER).current_streak_days,
    }


def _weekly_summary_outcome(family: str, activity_scenario: str, gap_scenario: str) -> dict:
    activity = _build_activity(family, activity_scenario)
    gap_report = _build_gap(family, gap_scenario)
    clock = FixedClock(SUMMARY_MONDAY)
    streaks = InMemoryStreakRepository()
    streaks.save(
        StreakRecord(
            user_id=USER,
            current_streak_days=6,
            longest_streak_days=11,
            last_activity_at=SCENARIO_NOW,
            streak_started_at=SCENARIO_NOW - timedelta(days=5),
            freeze_available=True,
            freeze_used_at=None,
            updated_at=SCENARIO_NOW,
        )
    )
    service = WeeklySummaryService(
        clock=clock,
        activity=activity,
        gap_report=gap_report,
        streaks=streaks,
        summaries=InMemoryWeeklySummaryRepository(),
        notifications=RecordingNotificationSink(),
        summary_day=Weekday.MONDAY,
    )
    summary = service.generate(USER).generated
    assert summary is not None
    payload = summary.model_dump(mode="json")
    # summary_id embeds only the account and the week, which are family-agnostic.
    return payload


def test_more_than_one_family_is_available_to_compare():
    families = _families()
    assert len(families) >= 2, f"families declaring the behavioural set: {families}"
    assert "mock" in families
    assert any(name != "mock" for name in families)


def test_record_activity_is_identical_across_adapter_families():
    families = _families()
    for scenario in BEHAVIOURAL_ACTIVITY_SCENARIOS:
        results = {family: _record_activity_outcome(family, scenario) for family in families}
        distinct = {repr(sorted(value.items())) for value in results.values()}
        assert len(distinct) == 1, f"scenario {scenario!r} diverged across families: {results}"


def test_the_boundary_holds_in_both_families():
    """The headline rule, proved once per family with no code change between."""
    for family in _families():
        inside = _record_activity_outcome(family, "activity_23h59m_ago")
        assert inside["outcome"] == "incremented", family
        assert inside["current_streak_days"] == 5, family

        outside = _record_activity_outcome(family, "activity_24h01m_ago")
        assert outside["outcome"] == "reset", family
        assert outside["current_streak_days"] == 1, family
        assert outside["longest_streak_days"] == 4, family


def test_badge_thresholds_hold_in_both_families():
    from uc08.ports.conformance import BEHAVIOURAL_QUESTION_COUNTS

    expected = {
        9: [],
        10: [10],
        11: [10],
        49: [10],
        50: [10, 50],
        99: [10, 50],
        100: [10, 50, 100],
        150: [10, 50, 100],
    }
    for family in _families():
        for count in BEHAVIOURAL_QUESTION_COUNTS:
            outcome = _record_activity_outcome(family, f"question_count_{count}")
            assert outcome["awarded_milestones"] == expected[count], (family, count)
            assert outcome["question_count"] == count, (family, count)


def test_weekly_summary_is_identical_across_adapter_families():
    families = _families()
    results = {
        family: _weekly_summary_outcome(family, "activity_23h59m_ago", "suggestion_available")
        for family in families
    }
    distinct = {repr(sorted(value.items())) for value in results.values()}
    assert len(distinct) == 1, f"weekly summaries diverged across families: {results}"

    sample = next(iter(results.values()))
    assert sample["current_streak_days"] == 6
    assert sample["questions_asked"] == 1
    assert sample["suggested_topic"]["naric_level"] == "level_6"
    assert sample["suggested_topic"]["explanation_profile"] == "intermediate"
    assert sample["suggested_topic"]["course_progress_percent"] == 64


def test_a_degraded_upstream_degrades_identically_across_families():
    families = _families()
    results = {
        family: _weekly_summary_outcome(family, "activity_23h59m_ago", "unavailable")
        for family in families
    }
    distinct = {repr(sorted(value.items())) for value in results.values()}
    assert len(distinct) == 1, f"degraded summaries diverged: {results}"

    sample = next(iter(results.values()))
    assert sample["suggested_topic"] is None
    assert sample["suggested_topic_status"] == "unavailable"
    assert "suggested_topic" in sample["omissions"]


def test_the_service_never_imports_an_adapter():
    """Structural companion: nothing above the port knows a family exists."""
    import pathlib

    forbidden = ("adapters.mock", "adapters.foreign", "adapters.real", "lexicon")
    root = pathlib.Path(__file__).resolve().parents[2] / "uc08"
    for layer in ("domain", "application", "api"):
        for path in (root / layer).rglob("*.py"):
            source = path.read_text(encoding="utf-8").lower()
            for token in forbidden:
                assert token not in source, f"{path} references {token}"
