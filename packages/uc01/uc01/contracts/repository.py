"""Persistence contract.

UC-01 business logic depends on this interface only. The shipped SQLite implementation
is a *standalone development store*; the company platform store can replace it by
implementing the same three methods plus the event append.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..domain.models import SessionEvent, SessionRecord


@runtime_checkable
class SessionRepository(Protocol):
    """Session record persistence.

    Implementations must be write-first-and-durable: :meth:`create` is called before any
    external dependency is touched, so that a record exists for every open attempt.
    """

    def create(self, record: SessionRecord) -> SessionRecord: ...

    def update(self, record: SessionRecord) -> SessionRecord: ...

    def get(self, session_id: str) -> SessionRecord | None: ...

    def list_for_user(self, user_id: str, limit: int = 50) -> Sequence[SessionRecord]: ...

    def append_event(self, event: SessionEvent) -> SessionEvent: ...

    def list_events(self, session_id: str) -> Sequence[SessionEvent]: ...


__all__ = ["SessionRepository"]
