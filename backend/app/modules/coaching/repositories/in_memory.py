"""In-memory coaching storage.

**This is not a database.** Dictionaries guarded by an ``asyncio.Lock``, satisfying the protocols
in ``protocols.py``. The real implementation over UC-07's ``qk_`` tables is in ``sqlalchemy.py``,
and it is what the application binds; this is what the **coaching tests** run against, and what a
standalone deployment of the module would run on.

It lives in ``app`` rather than in ``tests`` for that second reason: it is the default binding in
``create_container``, so the module is runnable without the rest of the system.

The uniqueness rule is enforced here exactly as the database enforces it — an index on
``(learner_id, attempt_id, question_id)`` — because the idempotency tests must exercise the real
constraint rather than a politer in-memory approximation (§30). That is what makes those tests
meaningful evidence about the SQLAlchemy implementation and not only about this one.
"""

from __future__ import annotations

import asyncio

from app.modules.coaching.domain.errors import (
    CoachingSessionNotFoundError,
    DuplicateCoachingSessionError,
)
from app.modules.coaching.domain.session import CoachingSession
from app.modules.coaching.domain.transcript import ChatMessage, CoachingTranscript


class InMemoryCoachingSessionRepository:
    """Sessions by id, with a unique index on the natural key."""

    def __init__(self) -> None:
        self._by_id: dict[str, CoachingSession] = {}
        self._by_key: dict[tuple[str, str, str], str] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: str) -> CoachingSession | None:
        async with self._lock:
            return self._by_id.get(session_id)

    async def get_for_learner(self, learner_id: str, session_id: str) -> CoachingSession | None:
        async with self._lock:
            session = self._by_id.get(session_id)
            # Filtered on ownership, not merely checked afterwards by the caller (§9).
            return session if session and session.learner_id == learner_id else None

    async def find_open(
        self, learner_id: str, attempt_id: str, question_id: str
    ) -> CoachingSession | None:
        async with self._lock:
            session_id = self._by_key.get((learner_id, attempt_id, question_id))
            return self._by_id.get(session_id) if session_id else None

    async def list_for_attempt(
        self, learner_id: str, attempt_id: str
    ) -> tuple[CoachingSession, ...]:
        async with self._lock:
            found = [
                session
                for session in self._by_id.values()
                if session.learner_id == learner_id and session.attempt_id == attempt_id
            ]
        # Deterministic order: delivery position, then question id for anything unpositioned.
        found.sort(key=lambda item: (item.question_position or 0, item.question_id))
        return tuple(found)

    async def insert(self, session: CoachingSession) -> CoachingSession:
        async with self._lock:
            if session.natural_key in self._by_key:
                raise DuplicateCoachingSessionError(*session.natural_key)
            self._by_id[session.session_id] = session
            self._by_key[session.natural_key] = session.session_id
            return session

    async def update(self, session: CoachingSession) -> CoachingSession:
        async with self._lock:
            existing = self._by_id.get(session.session_id)
            if existing is None:
                raise CoachingSessionNotFoundError(session.session_id)
            if existing.natural_key != session.natural_key:
                # A real database would reject this through the unique index; refusing here keeps
                # the in-memory behaviour honest rather than convenient.
                raise DuplicateCoachingSessionError(*session.natural_key)
            self._by_id[session.session_id] = session
            return session


class InMemoryCoachingTranscriptRepository:
    """Append-only conversation storage, keyed by session id."""

    def __init__(self) -> None:
        self._by_session: dict[str, CoachingTranscript] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: str) -> CoachingTranscript:
        async with self._lock:
            return self._by_session.get(session_id) or CoachingTranscript(session_id=session_id)

    async def append(self, session_id: str, *messages: ChatMessage) -> CoachingTranscript:
        async with self._lock:
            current = self._by_session.get(session_id) or CoachingTranscript(session_id=session_id)
            updated = current.appended(*messages)
            self._by_session[session_id] = updated
            return updated
