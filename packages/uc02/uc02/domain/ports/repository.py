"""Persistence port for assembled contexts.

UC-02 assumes no production database. Everything persistence-shaped lives behind
this one interface; the shipped implementation is in-memory with a TTL. The
company's storage layer replaces the implementation, not the interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from uc02.domain.models.context import SessionContext


class SessionContextRepository(ABC):
    @abstractmethod
    async def save(self, context: SessionContext) -> None:
        """Store (or overwrite) the context for ``context.session_id``."""

    @abstractmethod
    async def get(self, session_id: str) -> SessionContext | None:
        """Return the stored context, or ``None`` if absent or expired.

        Implementations must not raise for a missing key, and must treat an
        expired entry as absent.
        """

    @abstractmethod
    async def delete(self, session_id: str) -> bool:
        """Remove the context. Returns True if something was removed."""
