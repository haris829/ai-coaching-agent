"""Clock port. UTC only.

A summary states the instant it covers interactions through, and that instant
appears on a document a regulator may read. It comes from a port so that it is
testable and so that no module reaches for wall-clock time on its own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Supplies the current instant."""

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        ...
