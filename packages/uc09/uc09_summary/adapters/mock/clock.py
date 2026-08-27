"""Fixed clock for tests and for reproducible local runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

#: Default instant. Chosen to sit inside the mock session windows so that a
#: partial summary generated with this clock covers part, not all, of a session.
DEFAULT_NOW = datetime(2026, 3, 4, 9, 25, 0, tzinfo=UTC)


class FixedClock:
    """A clock that returns a fixed instant, optionally advancing on demand."""

    @classmethod
    def from_settings(cls, settings: object) -> FixedClock:
        return cls()

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or DEFAULT_NOW

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        """Test-support: move the clock."""
        self._now = value

    def advance(self, seconds: float) -> None:
        """Test-support: move the clock forward."""
        self._now = self._now + timedelta(seconds=seconds)
