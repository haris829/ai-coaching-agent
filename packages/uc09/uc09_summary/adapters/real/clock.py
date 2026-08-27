"""System clock. UTC only."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Returns the current UTC instant."""

    @classmethod
    def from_settings(cls, settings: object) -> SystemClock:
        return cls()

    def now(self) -> datetime:
        return datetime.now(UTC)
