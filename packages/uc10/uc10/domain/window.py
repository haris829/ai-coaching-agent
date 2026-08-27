"""The rolling evaluation window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("window bounds must be timezone-aware")
        if self.end < self.start:
            raise ValueError("window end precedes window start")

    @classmethod
    def rolling(cls, now: datetime, days: int) -> Window:
        """The rolling window ending *now*. ``days`` comes from configuration, never a
        constant in business logic."""
        if days < 1:
            raise ValueError("window length must be at least one day")
        end = now.astimezone(UTC)
        return cls(start=end - timedelta(days=days), end=end)

    def contains(self, moment: datetime) -> bool:
        """Half-open at neither end: a rating exactly on a bound is inside the window."""
        return self.start <= moment.astimezone(UTC) <= self.end

    def overlaps(self, other: Window) -> bool:
        return self.start <= other.end and other.start <= self.end
