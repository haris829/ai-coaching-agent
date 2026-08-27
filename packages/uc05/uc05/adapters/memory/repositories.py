"""Lightweight local persistence.

No production database, no ORM, no company schema.  These implementations exist
so that the domain can be exercised end to end; an integration engineer
replaces them through the registry.

They store deep copies.  Handing out a live reference would let a caller mutate
persisted state without going through the state machine, which would defeat the
whole point of persisting it.
"""

from __future__ import annotations

import asyncio

from ...domain.models import Dialogue, InteractionLogRecord, ModeState
from ...registry import (
    DIALOGUE_REPOSITORY_REGISTRY,
    INTERACTION_LOG_REPOSITORY_REGISTRY,
    SESSION_MODE_REPOSITORY_REGISTRY,
)


@DIALOGUE_REPOSITORY_REGISTRY.register("memory")
class InMemoryDialogueRepository:
    def __init__(self, **_: object) -> None:
        self._by_id: dict[str, Dialogue] = {}
        self._lock = asyncio.Lock()

    async def save(self, dialogue: Dialogue) -> None:
        async with self._lock:
            self._by_id[dialogue.dialogue_id] = dialogue.model_copy(deep=True)

    async def get(self, dialogue_id: str) -> Dialogue | None:
        async with self._lock:
            found = self._by_id.get(dialogue_id)
            return found.model_copy(deep=True) if found else None

    async def for_session(self, session_id: str) -> list[Dialogue]:
        async with self._lock:
            return [
                dialogue.model_copy(deep=True)
                for dialogue in self._by_id.values()
                if dialogue.session_id == session_id
            ]


@SESSION_MODE_REPOSITORY_REGISTRY.register("memory")
class InMemorySessionModeRepository:
    """The seam an integration engineer repoints at the company session store.

    Note what it does *not* do: it never invents a default.  A session it has
    never seen returns ``None``, and the application supplies
    ``ModeState.default_for(...)``.  If this class guessed a default, two
    implementations of the port could disagree about what "unset" means.
    """

    def __init__(self, **_: object) -> None:
        self._by_session: dict[str, ModeState] = {}
        self._lock = asyncio.Lock()

    async def get_mode(self, session_id: str) -> ModeState | None:
        async with self._lock:
            found = self._by_session.get(session_id)
            return found.model_copy(deep=True) if found else None

    async def set_mode(
        self, session_id: str, enabled: bool, owner_user_id: str
    ) -> ModeState:
        from ...domain.enums import ModeSource
        from ...domain.models import utcnow

        async with self._lock:
            existing = self._by_session.get(session_id)
            state = ModeState(
                session_id=session_id,
                enabled=enabled,
                source=ModeSource.PERSISTED,
                owner_user_id=(
                    existing.owner_user_id if existing and existing.owner_user_id
                    else owner_user_id
                ),
                updated_at=utcnow(),
            )
            self._by_session[session_id] = state
            return state.model_copy(deep=True)


@INTERACTION_LOG_REPOSITORY_REGISTRY.register("memory")
class InMemoryInteractionLogRepository:
    def __init__(self, **_: object) -> None:
        self._records: list[InteractionLogRecord] = []
        self._lock = asyncio.Lock()

    async def append(self, record: InteractionLogRecord) -> None:
        async with self._lock:
            self._records.append(record.model_copy(deep=True))

    async def get(self, interaction_id: str) -> InteractionLogRecord | None:
        async with self._lock:
            for record in self._records:
                if record.interaction_id == interaction_id:
                    return record.model_copy(deep=True)
            return None

    async def list_for_session(self, session_id: str) -> list[InteractionLogRecord]:
        async with self._lock:
            return [
                record.model_copy(deep=True)
                for record in self._records
                if record.session_id == session_id
            ]

    async def all_records(self) -> list[InteractionLogRecord]:
        """Test/diagnostic helper.  Not part of the port."""
        async with self._lock:
            return [record.model_copy(deep=True) for record in self._records]
