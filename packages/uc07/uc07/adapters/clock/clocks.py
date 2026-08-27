"""Clock adapters."""

from __future__ import annotations

from datetime import datetime, timezone

from uc07.ports.identity import Clock


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(tz=timezone.utc)


class FixedClock(Clock):
    """Deterministic clock for tests and reproducible local runs."""

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._moment = moment.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._moment
