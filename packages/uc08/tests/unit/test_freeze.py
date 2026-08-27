"""Streak freeze: eligibility, the UTC calendar month, and offer expiry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from uc08.domain.enums import FreezeOfferStatus, StreakOutcome
from uc08.domain.errors import FreezeNotAvailable
from uc08.domain.models import StreakRecord
from tests.conftest import USER, build_harness


def _seed(harness, *, current: int, hours_ago: int, freeze_used_at=None) -> StreakRecord:
    now = harness.clock.now()
    record = StreakRecord(
        user_id=USER,
        current_streak_days=current,
        longest_streak_days=current,
        last_activity_at=now - timedelta(hours=hours_ago),
        streak_started_at=now - timedelta(days=current),
        freeze_available=freeze_used_at is None,
        freeze_used_at=freeze_used_at,
        updated_at=now - timedelta(hours=hours_ago),
    )
    harness.streaks.save(record)
    return record


def test_a_missed_day_at_seven_days_is_offered_a_freeze(clock):
    harness = build_harness(clock)
    _seed(harness, current=7, hours_ago=48)

    result = harness.record("back-after-a-missed-day")

    assert result.outcome is StreakOutcome.RESET
    assert result.streak.current_streak_days == 1
    offer = result.freeze_offer
    assert offer is not None
    assert offer.status is FreezeOfferStatus.OFFERED
    assert offer.preserved_streak_days == 7
    assert offer.expires_at == clock.now() + timedelta(hours=24)


@pytest.mark.parametrize("streak_days", [1, 3, 6])
def test_a_missed_day_below_seven_days_is_not_offered_a_freeze(clock, streak_days):
    harness = build_harness(clock)
    _seed(harness, current=streak_days, hours_ago=48)

    result = harness.record("back-after-a-missed-day")

    assert result.outcome is StreakOutcome.RESET
    assert result.freeze_offer is None
    assert result.streak.current_streak_days == 1


def test_accepting_a_freeze_restores_the_streak_and_counts_today(clock):
    harness = build_harness(clock)
    _seed(harness, current=7, hours_ago=48)
    harness.record("back-after-a-missed-day")

    state = harness.streak_service.accept_freeze(USER)

    # Seven days held, plus the day they came back on.
    assert state.streak.current_streak_days == 8
    assert state.streak.longest_streak_days == 8
    assert state.streak.freeze_available is False
    assert state.streak.freeze_used_at == clock.now()
    assert state.open_freeze_offer is None
    assert harness.freeze_offers.get_latest(USER).status is FreezeOfferStatus.ACCEPTED


def test_a_declined_freeze_leaves_the_streak_reset(clock):
    harness = build_harness(clock)
    _seed(harness, current=9, hours_ago=48)
    harness.record("back-after-a-missed-day")

    state = harness.streak_service.decline_freeze(USER)

    assert state.streak.current_streak_days == 1
    assert state.streak.freeze_used_at is None  # declining spends nothing
    assert harness.freeze_offers.get_latest(USER).status is FreezeOfferStatus.DECLINED
    with pytest.raises(FreezeNotAvailable):
        harness.streak_service.accept_freeze(USER)


def test_a_freeze_is_usable_only_once_per_utc_calendar_month(clock):
    harness = build_harness(clock)
    _seed(harness, current=7, hours_ago=48)
    harness.record("first-miss")
    harness.streak_service.accept_freeze(USER)
    assert harness.streaks.get(USER).current_streak_days == 8

    # Second miss, same UTC month: no offer, and the streak resets normally.
    clock.advance(days=3)
    second = harness.record("second-miss-same-month")

    assert second.outcome is StreakOutcome.RESET
    assert second.streak.current_streak_days == 1
    assert second.freeze_offer is None
    with pytest.raises(FreezeNotAvailable):
        harness.streak_service.accept_freeze(USER)


def test_the_allowance_returns_in_the_next_utc_calendar_month(clock):
    harness = build_harness(clock)
    _seed(harness, current=7, hours_ago=48)
    harness.record("first-miss")
    harness.streak_service.accept_freeze(USER)

    # Move into the next UTC month and rebuild a qualifying streak.
    clock.set(datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc))
    _seed(harness, current=7, hours_ago=48, freeze_used_at=datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc))

    result = harness.record("miss-in-the-new-month")

    assert result.freeze_offer is not None
    assert result.freeze_offer.preserved_streak_days == 7
    state = harness.streak_service.accept_freeze(USER)
    assert state.streak.current_streak_days == 8


def test_the_calendar_month_boundary_is_utc(clock):
    """A freeze used at 23:30 UTC on the last day of a month does not block one
    on the first day of the next, and a local-time reading would disagree."""
    harness = build_harness(clock)
    clock.set(datetime(2026, 3, 31, 23, 30, tzinfo=timezone.utc))
    _seed(harness, current=7, hours_ago=48)
    harness.record("miss-at-month-end")
    harness.streak_service.accept_freeze(USER)

    # One hour later, in April: a fresh account state carrying the March usage.
    clock.set(datetime(2026, 4, 1, 0, 30, tzinfo=timezone.utc))
    april = build_harness(clock)
    _seed(
        april,
        current=7,
        hours_ago=48,
        freeze_used_at=datetime(2026, 3, 31, 23, 30, tzinfo=timezone.utc),
    )
    result = april.record("miss-one-hour-later-next-month")

    assert result.freeze_offer is not None
    assert april.streak_service.accept_freeze(USER).streak.current_streak_days == 8


def test_an_unanswered_offer_expires_and_does_not_preserve_the_streak(clock):
    harness = build_harness(clock)
    _seed(harness, current=7, hours_ago=48)
    offered = harness.record("back-after-a-missed-day")
    assert offered.freeze_offer is not None

    # One minute inside the 24-hour expiry: still acceptable.
    clock.advance(hours=23, minutes=59)
    assert harness.streak_service.get_state(USER).open_freeze_offer is not None

    # One minute past it: gone, and the streak is still the reset value.
    clock.advance(minutes=2)
    state = harness.streak_service.get_state(USER)
    assert state.open_freeze_offer is None
    assert state.streak.current_streak_days == 1
    assert harness.freeze_offers.get_latest(USER).status is FreezeOfferStatus.EXPIRED
    with pytest.raises(FreezeNotAvailable):
        harness.streak_service.accept_freeze(USER)


def test_an_expired_offer_does_not_spend_the_monthly_allowance(clock):
    harness = build_harness(clock)
    _seed(harness, current=7, hours_ago=48)
    harness.record("first-miss")
    clock.advance(hours=25)
    assert harness.streak_service.get_state(USER).open_freeze_offer is None

    _seed(harness, current=7, hours_ago=48)
    second = harness.record("second-miss")

    assert second.freeze_offer is not None
    assert harness.streak_service.accept_freeze(USER).streak.current_streak_days == 8


def test_accepting_without_an_offer_is_refused(clock):
    harness = build_harness(clock)
    _seed(harness, current=3, hours_ago=1)

    with pytest.raises(FreezeNotAvailable):
        harness.streak_service.accept_freeze(USER)


def test_a_freeze_offer_write_failure_does_not_block_coaching(clock):
    from uc08.domain.errors import RepositoryWriteFailed

    harness = build_harness(clock)
    _seed(harness, current=7, hours_ago=48)

    def explode(offer):
        raise RepositoryWriteFailed("offer store down")

    harness.freeze_offers.save = explode  # type: ignore[method-assign]

    result = harness.record("back-after-a-missed-day")

    # The streak work completed; only the incentive was lost.
    assert result.outcome is StreakOutcome.RESET
    assert result.streak.current_streak_days == 1
    assert harness.streaks.get(USER).current_streak_days == 1


def test_freeze_thresholds_come_from_configuration(clock):
    from uc08.config import load_settings

    harness = build_harness(clock, settings=load_settings(FREEZE_MIN_STREAK_DAYS=3, FREEZE_OFFER_EXPIRY_HOURS=6))
    _seed(harness, current=3, hours_ago=48)

    result = harness.record("back-after-a-missed-day")

    assert result.freeze_offer is not None
    assert result.freeze_offer.expires_at == clock.now() + timedelta(hours=6)
