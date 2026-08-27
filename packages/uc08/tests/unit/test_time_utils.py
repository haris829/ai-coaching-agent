"""UTC arithmetic: windows, calendar days, calendar months, ISO weeks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from uc08.adapters.clock.clocks import FixedClock, SystemClock
from uc08.domain.time_utils import (
    ensure_utc,
    iso_week_key,
    previous_iso_week_bounds,
    same_utc_day,
    same_utc_month,
    start_of_iso_week,
    start_of_utc_day,
    utc_month_key,
)


def test_a_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 3, 10, 12, 0))


def test_an_offset_datetime_is_converted_not_relabelled():
    plus_five = datetime(2026, 3, 10, 17, 0, tzinfo=timezone(timedelta(hours=5)))
    assert ensure_utc(plus_five) == datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)


def test_calendar_day_comparison_is_utc():
    late = datetime(2026, 3, 10, 23, 30, tzinfo=timezone.utc)
    early = datetime(2026, 3, 11, 0, 30, tzinfo=timezone.utc)
    assert not same_utc_day(late, early)
    # One hour apart, two UTC days: the once-per-day rule uses this deliberately.
    assert early - late == timedelta(hours=1)


def test_calendar_month_comparison_is_utc():
    march = datetime(2026, 3, 31, 23, 30, tzinfo=timezone.utc)
    april = datetime(2026, 4, 1, 0, 30, tzinfo=timezone.utc)
    assert utc_month_key(march) == "2026-03"
    assert utc_month_key(april) == "2026-04"
    assert not same_utc_month(march, april)
    assert same_utc_month(march, datetime(2026, 3, 1, tzinfo=timezone.utc))


def test_iso_week_keys_and_bounds():
    tuesday = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
    assert iso_week_key(tuesday) == "2026-W11"
    assert start_of_iso_week(tuesday) == datetime(2026, 3, 9, tzinfo=timezone.utc)

    monday = datetime(2026, 3, 16, 9, 0, tzinfo=timezone.utc)
    start, end = previous_iso_week_bounds(monday)
    assert start == datetime(2026, 3, 9, tzinfo=timezone.utc)
    assert end == datetime(2026, 3, 16, tzinfo=timezone.utc)
    assert iso_week_key(start) == "2026-W11"


def test_iso_week_keys_are_sortable_across_a_year_boundary():
    keys = [
        iso_week_key(datetime(2026, 12, 28, tzinfo=timezone.utc)),
        iso_week_key(datetime(2027, 1, 4, tzinfo=timezone.utc)),
    ]
    assert keys == ["2026-W53", "2027-W01"]
    assert sorted(keys) == keys


def test_start_of_utc_day():
    assert start_of_utc_day(datetime(2026, 3, 10, 23, 59, 59, tzinfo=timezone.utc)) == datetime(
        2026, 3, 10, tzinfo=timezone.utc
    )


def test_the_fixed_clock_only_moves_when_told():
    clock = FixedClock(datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc))
    first = clock.now()
    assert clock.now() == first

    clock.advance(hours=23, minutes=59)
    assert clock.now() - first == timedelta(hours=23, minutes=59)

    clock.rewind(minutes=59)
    assert clock.now() - first == timedelta(hours=23)

    clock.set(datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert clock.now() == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_the_fixed_clock_rejects_a_naive_start():
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 3, 10, 12, 0))


def test_the_system_clock_returns_aware_utc():
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
