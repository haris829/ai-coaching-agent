"""The clock is a dependency.

Every rule in UC-08 is a statement about time, so no module in this component
calls the system clock. ``Clock.now()`` returns a timezone-aware UTC datetime
and is the single source of "now". Tests advance a fake clock; they never sleep.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class Clock(ABC):
    """Read-only source of the current UTC moment."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current moment as a timezone-aware UTC datetime."""
