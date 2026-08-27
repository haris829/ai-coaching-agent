"""Time and id generation contracts.

Injected rather than called directly so that session records are deterministic in tests
and so a distributed id scheme can replace UUIDs later without touching business logic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class IdGenerator(Protocol):
    def new_session_id(self) -> str: ...


class SystemClock:
    """UTC, timezone-aware. Naive datetimes are never produced by UC-01."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidIdGenerator:
    def new_session_id(self) -> str:
        return f"sess_{uuid.uuid4().hex}"


__all__ = ["Clock", "IdGenerator", "SystemClock", "UuidIdGenerator"]
