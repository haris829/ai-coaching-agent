"""UTC-only time helpers.

Nothing here reads a clock. The clock is a port (``uc08.ports.clock.Clock``).
All arithmetic in this component is UTC; local time is never introduced.
"""

from __future__ import annotations

from datetime import date, datetime, timezone


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    A naive datetime is rejected rather than silently assumed to be UTC: a
    silently-assumed offset is exactly the class of bug that destroys a streak.
    """
    if value.tzinfo is None:
        raise ValueError("naive datetime rejected; UC-08 arithmetic is UTC and requires tz-aware values")
    return value.astimezone(timezone.utc)


def utc_day(value: datetime) -> date:
    """The UTC calendar day a moment falls on."""
    return ensure_utc(value).date()


def same_utc_day(left: datetime, right: datetime) -> bool:
    return utc_day(left) == utc_day(right)


def utc_month_key(value: datetime) -> str:
    """``YYYY-MM`` in UTC. The calendar used for the freeze allowance (A-11)."""
    moment = ensure_utc(value)
    return f"{moment.year:04d}-{moment.month:02d}"


def same_utc_month(left: datetime, right: datetime) -> bool:
    return utc_month_key(left) == utc_month_key(right)


def iso_week_key(value: datetime) -> str:
    """``GGGG-Www`` ISO week key in UTC, e.g. ``2026-W12`` (A-14)."""
    moment = ensure_utc(value)
    iso_year, iso_week, _ = moment.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def start_of_utc_day(value: datetime) -> datetime:
    moment = ensure_utc(value)
    return datetime(moment.year, moment.month, moment.day, tzinfo=timezone.utc)


def start_of_iso_week(value: datetime) -> datetime:
    """00:00:00Z on the Monday of the ISO week containing ``value``."""
    midnight = start_of_utc_day(value)
    return midnight - _days(midnight.isoweekday() - 1)


def _days(count: int):
    from datetime import timedelta

    return timedelta(days=count)


def previous_iso_week_bounds(value: datetime) -> tuple[datetime, datetime]:
    """``[start, end)`` of the ISO week immediately before the one containing
    ``value``."""
    this_week_start = start_of_iso_week(value)
    return this_week_start - _days(7), this_week_start
