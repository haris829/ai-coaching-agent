"""Clock adapters.

``SystemClock`` is the only place in this repository that reads the machine
clock. Everything else takes a :class:`~uc08.ports.clock.Clock`.
``tests/architecture/test_clock_is_injected.py`` asserts that.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from uc08.domain.time_utils import ensure_utc
from uc08.ports.clock import Clock


class SystemClock(Clock):
    """Wall-clock UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock(Clock):
    """A clock that only moves when told to.

    Tests drive the 23h59m / 24h01m boundary by advancing this, never by
    sleeping. Shipped with the component (not hidden in the test tree) so the
    mock matrix in ``docs/SHARED_CONTRACT.md`` is reproducible by an integrator.
    """

    def __init__(self, start: datetime) -> None:
        self._now = ensure_utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> None:
        self._now = ensure_utc(moment)

    def advance(
        self,
        *,
        days: int = 0,
        hours: int = 0,
        minutes: int = 0,
        seconds: int = 0,
        weeks: int = 0,
    ) -> datetime:
        self._now = self._now + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds, weeks=weeks)
        return self._now

    def rewind(self, **kwargs: int) -> datetime:
        negated = {key: -value for key, value in kwargs.items()}
        return self.advance(**negated)
