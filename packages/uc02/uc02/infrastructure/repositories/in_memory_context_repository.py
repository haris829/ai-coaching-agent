"""In-memory implementation of ``SessionContextRepository``.

UC-02 assumes no production database: no migrations, no ORM, no schema. This
class is the entire persistence layer and it is the only thing the integration
engineer replaces when the company's storage arrives (see docs/integration.md).

Behaviour worth knowing before replacing it:

* Entries expire after ``CONTEXT_TTL_HOURS`` so the process does not grow without
  bound. Expiry is evaluated lazily on read plus a sweep on write.
* An expired entry is indistinguishable from an absent one to callers, which
  means a session whose context has expired will be rebuilt on the next
  initialize. The company's layer will likely handle expiry differently
  (row TTL, background job, or none at all) -- that is a documented assumption.
* Not shared across processes. Two workers will each build their own context for
  the same session, so the "no re-query" guarantee holds per process only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from uc02.domain.models.context import SessionContext
from uc02.domain.ports.repository import SessionContextRepository

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _Entry:
    context: SessionContext
    expires_at: datetime


class InMemorySessionContextRepository(SessionContextRepository):
    def __init__(self, ttl_hours: int = 12, clock: Clock = _utc_now) -> None:
        self._ttl = timedelta(hours=ttl_hours)
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def save(self, context: SessionContext) -> None:
        async with self._lock:
            self._sweep()
            self._entries[context.session_id] = _Entry(
                context=context, expires_at=self._clock() + self._ttl
            )

    async def get(self, session_id: str) -> SessionContext | None:
        async with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                del self._entries[session_id]
                return None
            return entry.context

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            return self._entries.pop(session_id, None) is not None

    # -- test / operational helpers (not part of the port) ----------------
    def size(self) -> int:
        return len(self._entries)

    def peek(self, session_id: str) -> SessionContext | None:
        """Synchronous read of stored state, ignoring expiry.

        For tests and operational inspection only. Callers must use ``get``.
        """
        entry = self._entries.get(session_id)
        return entry.context if entry else None

    def _sweep(self) -> None:
        now = self._clock()
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            del self._entries[key]
