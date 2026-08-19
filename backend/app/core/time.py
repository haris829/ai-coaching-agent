"""The one clock, and the one wire format for instants.

Every timestamp in this system is timezone-aware UTC, and that is enforced rather than hoped for:

* :class:`Clock` is **injected**, so timing behaviour is asserted deterministically instead of by
  sleeping. UC-03's expiry rules depend on this — a test advances a :class:`FixedClock` and the
  server's answer is exact.
* :func:`ensure_utc` treats a naive datetime as an **error**, not an assumption. Silently reading it
  as UTC (or as local time) is the class of bug that lets quiz timing drift.
* :class:`app.db.types.UtcDateTime` applies the same rule at the persistence boundary, so a naive
  value cannot even be written.
* :func:`to_iso` gives one canonical wire format (``…Z``), so API responses are unambiguous and
  directly comparable by clients.

Nothing here may read a client-supplied time. A client's clock is echoed back as advisory skew and
never enters a calculation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Source of the current instant."""

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    """The real clock. Used in production."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A manually controlled clock for tests and deterministic verification.

    Time moves only when explicitly advanced or set, which makes expiry and autosave behaviour
    assertable without any real waiting.
    """

    __slots__ = ("_current",)

    def __init__(self, start: datetime | str = "2026-01-01T00:00:00+00:00") -> None:
        self._current = parse_instant(start) if isinstance(start, str) else ensure_utc(start)

    def now(self) -> datetime:
        return self._current

    def advance(self, *, seconds: float = 0, minutes: float = 0, hours: float = 0) -> FixedClock:
        """Move the clock forward. Refuses to move backwards."""
        delta = timedelta(seconds=seconds, minutes=minutes, hours=hours)
        if delta < timedelta(0):
            raise ValueError("FixedClock cannot move backwards via advance(); use set().")
        self._current += delta
        return self

    def set(self, instant: datetime | str) -> FixedClock:
        """Hard-set the clock, e.g. to simulate an operator or NTP correction."""
        self._current = parse_instant(instant) if isinstance(instant, str) else ensure_utc(instant)
        return self


def utcnow() -> datetime:
    """Timezone-aware UTC now.

    The default for column defaults and for code with no injected clock. Anything whose *timing
    behaviour* is under test should take a :class:`Clock` instead, so it can be controlled.
    """
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Normalise a datetime to timezone-aware UTC. A naive value is an error."""
    if value.tzinfo is None:
        raise ValueError("A timezone-aware datetime is required; received a naive value.")
    return value.astimezone(UTC)


def assume_utc(value: datetime) -> datetime:
    """Attach UTC to a value already known to be UTC but lacking an offset.

    Needed only when reading from a store that discards offsets — SQLite hands back naive
    datetimes. Distinct from :func:`ensure_utc` on purpose: this one is an explicit, narrow
    decision at a read boundary, not a silent assumption sprinkled through the code.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def parse_instant(value: str) -> datetime:
    """Parse an ISO-8601 instant, accepting a trailing ``Z``."""
    text = value.strip()
    if text.endswith(("z", "Z")):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp {value!r} is missing a timezone offset.")
    return parsed.astimezone(UTC)


def to_iso(value: datetime) -> str:
    """Render an instant as ISO-8601 UTC with a ``Z`` designator."""
    return ensure_utc(assume_utc(value)).isoformat().replace("+00:00", "Z")


def iso_or_none(value: datetime | None) -> str | None:
    return None if value is None else to_iso(value)
