"""In-memory implementation of :class:`uc01.contracts.repository.SessionRepository`.

Used by fast unit tests and by ``UC01_PERSISTENCE=memory``. It exists mainly as proof
that the repository contract, not SQLite, is what UC-01 depends on.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Sequence
from itertools import count

from ..domain.models import SessionEvent, SessionRecord


class InMemorySessionRepository:
    """Thread-safe dict-backed store. Contents are lost on process exit."""

    def __init__(self) -> None:
        self._records: dict[str, SessionRecord] = {}
        self._events: list[SessionEvent] = []
        self._event_ids = count(1)
        self._lock = threading.RLock()

    def create(self, record: SessionRecord) -> SessionRecord:
        with self._lock:
            self._records[record.session_id] = copy.deepcopy(record)
        return record

    def update(self, record: SessionRecord) -> SessionRecord:
        with self._lock:
            self._records[record.session_id] = copy.deepcopy(record)
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            record = self._records.get(session_id)
            return copy.deepcopy(record) if record else None

    def list_for_user(self, user_id: str, limit: int = 50) -> Sequence[SessionRecord]:
        with self._lock:
            matching = [
                copy.deepcopy(record)
                for record in self._records.values()
                if record.user_id == user_id
            ]
        matching.sort(key=lambda record: record.created_at, reverse=True)
        return tuple(matching[:limit])

    def append_event(self, event: SessionEvent) -> SessionEvent:
        with self._lock:
            stored = SessionEvent(
                session_id=event.session_id,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                payload=dict(event.payload),
                event_id=next(self._event_ids),
            )
            self._events.append(stored)
        return stored

    def list_events(self, session_id: str) -> Sequence[SessionEvent]:
        with self._lock:
            return tuple(
                event for event in self._events if event.session_id == session_id
            )


__all__ = ["InMemorySessionRepository"]
