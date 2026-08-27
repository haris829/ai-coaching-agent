"""The 24-hour boundary. Driven by the fake clock; nothing sleeps.

The two cases are deliberately built so that a calendar-day rule *cannot* tell
them apart: with a Tuesday 12:00 UTC anchor, activity 23h59m ago is Monday
12:01 UTC and activity 24h01m ago is Monday 11:59 UTC. Both are "yesterday".
Only the rolling 24-hour window separates them, and the specification says 24
hours.
"""

from __future__ import annotations

from datetime import timedelta

from uc08.adapters.mock.scenarios import activity_ledger_at_offset
from uc08.domain.enums import StreakOutcome
from uc08.domain.models import StreakRecord
from tests.conftest import ANCHOR, USER, build_harness


def _seed(harness, *, current: int, last_activity_at, longest: int | None = None) -> StreakRecord:
    record = StreakRecord(
        user_id=USER,
        current_streak_days=current,
        longest_streak_days=longest if longest is not None else current,
        last_activity_at=last_activity_at,
        streak_started_at=last_activity_at - timedelta(days=current - 1),
        freeze_available=True,
        freeze_used_at=None,
        updated_at=last_activity_at,
    )
    harness.streaks.save(record)
    return record


def test_both_boundary_cases_fall_on_the_same_calendar_day(clock):
    """Guard on the test itself: a calendar-day rule could not distinguish these."""
    inside = ANCHOR - timedelta(hours=23, minutes=59)
    outside = ANCHOR - timedelta(hours=24, minutes=1)
    assert inside.date() == outside.date()
    assert inside.date() != ANCHOR.date()


def test_activity_23h59m_ago_increments(clock):
    ledger = activity_ledger_at_offset(clock, user_id=USER, hours=23, minutes=59)
    harness = build_harness(clock, ledger=ledger)
    _seed(harness, current=4, last_activity_at=clock.now() - timedelta(hours=23, minutes=59))

    result = harness.record("interaction-inside-window")

    assert result.outcome is StreakOutcome.INCREMENTED
    assert result.streak.current_streak_days == 5
    assert result.streak.longest_streak_days == 5


def test_activity_24h01m_ago_resets(clock):
    ledger = activity_ledger_at_offset(clock, user_id=USER, hours=24, minutes=1)
    harness = build_harness(clock, ledger=ledger)
    _seed(harness, current=4, last_activity_at=clock.now() - timedelta(hours=24, minutes=1))

    result = harness.record("interaction-outside-window")

    assert result.outcome is StreakOutcome.RESET
    assert result.streak.current_streak_days == 1
    # The achievement survives the reset.
    assert result.streak.longest_streak_days == 4


def test_exactly_24h_ago_is_inside_the_window(clock):
    """The window is inclusive at 24h: ``now - last <= 24h`` qualifies.

    Stated so the choice is visible rather than incidental (A-03).
    """
    ledger = activity_ledger_at_offset(clock, user_id=USER, hours=24, minutes=0)
    harness = build_harness(clock, ledger=ledger)
    _seed(harness, current=2, last_activity_at=clock.now() - timedelta(hours=24))

    result = harness.record("interaction-at-exactly-24h")

    assert result.outcome is StreakOutcome.INCREMENTED
    assert result.streak.current_streak_days == 3


def test_boundary_is_driven_by_advancing_the_clock_not_by_waiting(clock):
    """Walk a streak forward a day at a time, then step one minute too far."""
    harness = build_harness(clock)

    first = harness.record("day-1")
    assert first.streak.current_streak_days == 1

    for day in range(2, 6):
        clock.advance(hours=23, minutes=59)
        result = harness.record(f"day-{day}")
        assert result.outcome is StreakOutcome.INCREMENTED
        assert result.streak.current_streak_days == day

    # 24h01m after the last interaction: outside the window.
    clock.advance(hours=24, minutes=1)
    broken = harness.record("after-the-gap")
    assert broken.outcome is StreakOutcome.RESET
    assert broken.streak.current_streak_days == 1
    assert broken.streak.longest_streak_days == 5


def test_window_hours_is_configuration_not_a_literal(clock):
    ledger = activity_ledger_at_offset(clock, user_id=USER, hours=40)
    from uc08.config import load_settings

    settings = load_settings(STREAK_WINDOW_HOURS=48)
    harness = build_harness(clock, ledger=ledger, settings=settings)
    _seed(harness, current=3, last_activity_at=clock.now() - timedelta(hours=40))

    result = harness.record("inside-a-48h-window")

    assert result.outcome is StreakOutcome.INCREMENTED
    assert result.streak.current_streak_days == 4
