"""Persistence ports.

No production database, no ORM, no company schema assumptions.  Each of these
is an interface with a lightweight local implementation in
``uc05/adapters/memory/``; an integration engineer repoints them at the
company's stores through the same registry mechanism as any other provider.

Repositories raise ``ProviderUnavailable`` / ``ProviderTimeout`` for
infrastructure failure and ``DialogueNotFound`` for a genuine miss.  They do
**not** enforce ownership -- that is the application's job and it is applied
uniformly on every read, so a repository implementation cannot forget it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import Dialogue, InteractionLogRecord, ModeState


@runtime_checkable
class DialogueRepository(Protocol):
    """Stores the dialogue aggregate -- the persisted state machine instance.

    Retained in full (guiding sequence and learner responses) for the
    improvement pipeline.  UC-05 exposes it to its owner only.
    """

    async def save(self, dialogue: Dialogue) -> None:
        ...

    async def get(self, dialogue_id: str) -> Dialogue | None:
        ...

    async def for_session(self, session_id: str) -> list[Dialogue]:
        ...


@runtime_checkable
class SessionModeRepository(Protocol):
    """Persists the Socratic mode flag against an opaque session id.

    UC-05 does not own the session record.  This port is the seam: the
    in-memory implementation keeps a dict; a company implementation writes the
    flag into the real session store.  Nothing else in UC-05 changes.

    ``get_mode`` returns ``None`` when the session has never had a mode set;
    the application supplies ``ModeState.default_for(session_id)``.  A
    repository must not invent a default of its own.
    """

    async def get_mode(self, session_id: str) -> ModeState | None:
        ...

    async def set_mode(
        self, session_id: str, enabled: bool, owner_user_id: str
    ) -> ModeState:
        ...


@runtime_checkable
class InteractionLogRepository(Protocol):
    """Append-only store of the platform's interaction log record.

    UC-05 writes ``rating_state = "pending"`` and never changes it.  Another
    component owns rating.
    """

    async def append(self, record: InteractionLogRecord) -> None:
        ...

    async def get(self, interaction_id: str) -> InteractionLogRecord | None:
        ...

    async def list_for_session(self, session_id: str) -> list[InteractionLogRecord]:
        ...
