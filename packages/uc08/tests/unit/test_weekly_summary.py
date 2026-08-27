"""Weekly summary: the four elements, delivery, retries, and no batch sending."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from uc08.adapters.mock.ledger import Fault
from uc08.adapters.sinks.local import FailingWeeklySummarySink
from uc08.domain.enums import DeliveryStatus, SourceStatus
from uc08.domain.models import StreakRecord
from uc08.domain.time_utils import iso_week_key, previous_iso_week_bounds
from tests.conftest import MONDAY, USER, build_harness

SUGGESTION = {
    "topic_id": "topic-solicitors-accounts",
    "name": "Solicitors Accounts Rules",
    "naric_level": "level_6",
    "course_progress_percent": 64,
}


def _seed_last_week(harness, *, topics=("conduct", "probate"), interactions: int = 5) -> None:
    """Put activity into the ISO week that just ended."""
    week_start, week_end = previous_iso_week_bounds(harness.clock.now())
    for index in range(interactions):
        moment = week_start + timedelta(days=index % 5, hours=10)
        harness.ledger.add_interaction(
            USER, moment, f"last-week-{index}", topic=topics[index % len(topics)]
        )
    assert week_end > week_start


def _seed_streak(harness, days: int) -> None:
    now = harness.clock.now()
    harness.streaks.save(
        StreakRecord(
            user_id=USER,
            current_streak_days=days,
            longest_streak_days=days,
            last_activity_at=now - timedelta(hours=2),
            streak_started_at=now - timedelta(days=days),
            freeze_available=True,
            freeze_used_at=None,
            updated_at=now,
        )
    )


def test_monday_generation_contains_all_four_elements(monday_clock):
    harness = build_harness(monday_clock)
    harness.gap_plan.set_suggestion(USER, dict(SUGGESTION))
    _seed_last_week(harness, interactions=5)
    _seed_streak(harness, 9)

    run = harness.summary_service.generate(USER)

    summary = run.generated
    assert summary is not None
    # 1: topics covered last week
    assert set(summary.topics_covered) == {"conduct", "probate"}
    assert summary.topics_status is SourceStatus.AVAILABLE
    # 2: questions asked
    assert summary.questions_asked == 5
    assert summary.questions_asked_status is SourceStatus.AVAILABLE
    # 3: current streak length
    assert summary.current_streak_days == 9
    # 4: a suggested topic drawn from the gap report port
    assert summary.suggested_topic is not None
    assert summary.suggested_topic.name == "Solicitors Accounts Rules"
    assert summary.suggested_topic.naric_level.value == "level_6"
    assert summary.suggested_topic.explanation_profile.value == "intermediate"
    assert summary.suggested_topic.course_progress_percent == 64
    assert summary.suggested_topic_status is SourceStatus.AVAILABLE
    assert summary.omissions == ()

    week_start, week_end = previous_iso_week_bounds(MONDAY)
    assert summary.week == iso_week_key(week_start)
    assert summary.week_start_at == week_start
    assert summary.week_end_at == week_end


def test_this_week_activity_is_not_counted_as_last_week(monday_clock):
    harness = build_harness(monday_clock)
    _seed_last_week(harness, topics=("conduct",), interactions=2)
    # Activity earlier this Monday morning, i.e. the current week.
    harness.ledger.add_interaction(USER, monday_clock.now() - timedelta(hours=1), "this-week", topic="tax")

    summary = harness.summary_service.generate(USER).generated

    assert summary is not None
    assert summary.questions_asked == 2
    assert "tax" not in summary.topics_covered


def test_the_record_is_written_and_logged_whether_sent_or_not(monday_clock):
    sink = FailingWeeklySummarySink(fail_sends=1)
    harness = build_harness(monday_clock, notifications=sink)
    _seed_last_week(harness)

    run = harness.summary_service.generate(USER)

    assert run.generated is not None
    assert run.generated.delivery_status is DeliveryStatus.FAILED
    assert run.generated.send_attempts == 1
    # Stored despite the delivery failure: the record is the deliverable.
    stored = harness.summaries.get(USER, run.generated.week)
    assert stored is not None
    assert stored.delivery_status is DeliveryStatus.FAILED
    assert sink.summary_events == []


def test_a_send_failure_retries_the_following_day(monday_clock):
    sink = FailingWeeklySummarySink(fail_sends=1)
    harness = build_harness(monday_clock, notifications=sink)
    _seed_last_week(harness)

    failed = harness.summary_service.generate(USER).generated
    assert failed is not None
    assert failed.next_retry_at == datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc)

    # Later the same Monday: not yet due, and no second attempt is made.
    monday_clock.advance(hours=6)
    same_day = harness.summary_service.generate(USER)
    assert same_day.retried is None
    assert sink.send_attempts == 1

    # Tuesday: the retry runs, and no new summary is generated.
    monday_clock.set(datetime(2026, 3, 17, 9, 0, tzinfo=timezone.utc))
    tuesday = harness.summary_service.generate(USER)
    assert tuesday.generated is None
    assert tuesday.already_generated is True
    assert tuesday.retried is not None
    assert tuesday.retried.delivery_status is DeliveryStatus.SENT
    assert tuesday.retried.send_attempts == 2
    assert len(sink.summary_events) == 1


def test_no_new_summary_is_generated_on_a_non_summary_day(monday_clock):
    harness = build_harness(monday_clock)
    _seed_last_week(harness)
    monday_clock.set(datetime(2026, 3, 18, 9, 0, tzinfo=timezone.utc))  # Wednesday

    run = harness.summary_service.generate(USER)

    assert run.generated is None
    assert "not the configured summary day" in run.reason
    assert harness.summaries.list_for_user(USER) == ()


def test_a_four_week_absence_produces_one_summary_not_four(monday_clock):
    harness = build_harness(monday_clock)
    _seed_last_week(harness)

    first = harness.summary_service.generate(USER)
    assert first.generated is not None
    first_week = first.generated.week

    # No generation call for four weeks, then one call on a Monday.
    monday_clock.advance(weeks=4)
    late = harness.summary_service.generate(USER)

    assert late.generated is not None
    assert late.generated.week != first_week
    assert len(harness.summaries.list_for_user(USER)) == 2  # one per call, never a backlog
    assert len(harness.notifications.summary_events) == 2
    # The weeks that went by are named, not generated.
    assert len(late.generated.skipped_weeks) == 3
    assert first_week not in late.generated.skipped_weeks
    assert late.generated.week not in late.generated.skipped_weeks
    for week in late.generated.skipped_weeks:
        assert harness.summaries.get(USER, week) is None


def test_repeated_generation_on_the_same_monday_does_not_duplicate(monday_clock):
    harness = build_harness(monday_clock)
    _seed_last_week(harness)

    first = harness.summary_service.generate(USER)
    second = harness.summary_service.generate(USER)
    third = harness.summary_service.generate(USER)

    assert first.generated is not None
    assert second.generated is None and second.already_generated is True
    assert third.generated is None and third.already_generated is True
    assert len(harness.summaries.list_for_user(USER)) == 1
    assert len(harness.notifications.summary_events) == 1


def test_an_unavailable_gap_report_omits_the_suggestion_and_says_so(monday_clock):
    harness = build_harness(monday_clock)
    _seed_last_week(harness)
    harness.gap_plan.with_fault(Fault.UNAVAILABLE)

    summary = harness.summary_service.generate(USER).generated

    assert summary is not None
    assert summary.suggested_topic is None
    assert summary.suggested_topic_status is SourceStatus.UNAVAILABLE
    assert "suggested_topic" in summary.omissions
    assert any("No suggestion was invented" in note for note in summary.omission_notes)
    # The other three elements are still there.
    assert summary.questions_asked == 5
    assert summary.topics_covered


def test_no_suggestion_is_empty_not_unavailable(monday_clock):
    harness = build_harness(monday_clock)
    _seed_last_week(harness)
    harness.gap_plan.set_suggestion(USER, None)

    summary = harness.summary_service.generate(USER).generated

    assert summary is not None
    assert summary.suggested_topic is None
    assert summary.suggested_topic_status is SourceStatus.EMPTY
    assert any("had no suggestion" in note for note in summary.omission_notes)


def test_an_unavailable_activity_source_still_produces_a_record(monday_clock):
    harness = build_harness(monday_clock)
    harness.gap_plan.set_suggestion(USER, dict(SUGGESTION))
    _seed_streak(harness, 4)
    harness.ledger.with_fault(Fault.UNAVAILABLE)

    summary = harness.summary_service.generate(USER).generated

    assert summary is not None
    assert summary.topics_covered == ()
    assert summary.topics_status is SourceStatus.UNAVAILABLE
    assert summary.questions_asked == 0
    assert summary.questions_asked_status is SourceStatus.UNAVAILABLE
    assert set(summary.omissions) == {"topics_covered", "questions_asked"}
    assert summary.current_streak_days == 4
    assert summary.suggested_topic is not None


def test_an_empty_week_is_empty_not_unavailable(monday_clock):
    harness = build_harness(monday_clock)
    harness.gap_plan.set_suggestion(USER, dict(SUGGESTION))

    summary = harness.summary_service.generate(USER).generated

    assert summary is not None
    assert summary.topics_status is SourceStatus.EMPTY
    assert summary.questions_asked_status is SourceStatus.EMPTY
    assert summary.questions_asked == 0
    assert "topics_covered" not in summary.omissions


def test_an_unmappable_naric_level_degrades_to_the_platform_default(monday_clock):
    harness = build_harness(monday_clock)
    _seed_last_week(harness)
    harness.gap_plan.set_suggestion(
        USER, {**SUGGESTION, "naric_level": "masters-ish", "course_progress_percent": "64%"}
    )

    summary = harness.summary_service.generate(USER).generated

    assert summary is not None
    topic = summary.suggested_topic
    assert topic is not None
    assert topic.naric_level.value == "level_5"
    assert topic.naric_level_source.value == "default"
    assert topic.naric_level_status.value == "invalid"
    assert topic.explanation_profile.value == "intermediate"
    assert topic.course_progress_percent == 64


def test_summaries_are_listed_most_recent_first(monday_clock):
    harness = build_harness(monday_clock)
    _seed_last_week(harness)
    harness.summary_service.generate(USER)
    monday_clock.advance(weeks=1)
    harness.summary_service.generate(USER)

    listed = harness.summary_service.list_for_user(USER)

    assert len(listed) == 2
    assert listed[0].week > listed[1].week


def test_one_account_summaries_are_not_visible_to_another(monday_clock):
    harness = build_harness(monday_clock)
    _seed_last_week(harness)
    harness.summary_service.generate(USER)

    assert harness.summary_service.list_for_user("someone-else") == ()
