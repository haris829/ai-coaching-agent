"""The company-database adapters for UC-07's four persistence ports.

    CoachingSessionRepository     -> qk_coaching_sessions
    CoachingTranscriptRepository  -> qk_coaching_messages
    KnowledgeGapTracker           -> qk_knowledge_gaps
    CoachingActivityLog           -> qk_coaching_activity

UC-07 was written against the protocols; this is the implementation they were waiting for. Nothing
in the domain, the services or their tests changed to accommodate it — that claim is what
``tests/coaching/`` and ``tests/integration/test_coaching_chain.py`` between them check, the first
against in-memory doubles and the second against these classes over a real database.

WHY THESE ARE ``async def`` OVER A SYNCHRONOUS SESSION
-----------------------------------------------------
UC-07's ports are asynchronous because the thing they were shaped around — an AI provider over the
network — is. The rest of this application is synchronous SQLAlchemy, and the two meet here.

Every database call is handed to a worker thread by :func:`offload`. Doing the work inline would
block the event loop for the duration of the query, which on a shared loop means stalling every
*other* in-flight request; the rest of the application avoids that by having FastAPI run its ``def``
endpoints in a threadpool, and this is the same trick applied by hand. The session is never touched
concurrently, because each call is awaited before the next begins.

TRANSACTIONS
------------
These methods **commit their own work**, like every other service in this application. Coaching is
a sequence of independent facts — a session opened, a message stored, an event recorded — and a
learner who loses their connection mid-exchange should keep the message they sent. Wrapping the
whole request in one transaction would silently discard it.

The two outbound streams isolate their own failures rather than raising, because §21 and §22 are
explicit that analytics must never break coaching: a knowledge-gap store that is down produces a log
line, not a learner losing their session. The session and transcript repositories do the opposite —
they raise, because losing those *is* losing the conversation.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.async_db import offload
from app.core.errors import PersistenceFailedError
from app.core.logging import get_logger
from app.core.time import parse_instant, to_iso, utcnow
from app.modules.coaching.domain.enums import CoachingMode, CoachingSessionStatus, MessageRole
from app.modules.coaching.domain.errors import (
    CoachingSessionNotFoundError,
    DuplicateCoachingSessionError,
)
from app.modules.coaching.domain.session import CoachingSession
from app.modules.coaching.domain.transcript import ChatMessage, CoachingTranscript
from app.modules.coaching.integration.activity import CoachingActivityEvent
from app.modules.coaching.integration.knowledge_gaps import KnowledgeGapEvent
from app.modules.coaching.models import (
    CoachingActivityRow,
    CoachingMessageRow,
    CoachingSessionRow,
    KnowledgeGapRow,
)

logger = get_logger(__name__)

#: Re-exported so UC-07's adapters keep importing it from here, where they always have. The
#: helper itself moved to ``app.core.async_db`` when UC-08, UC-09 and UC-10 arrived needing the
#: same bridge — one implementation of it, rather than four.
__all__ = ["offload"]


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


def _to_session(row: CoachingSessionRow) -> CoachingSession:
    """One row as the immutable domain value the services work with."""
    return CoachingSession(
        session_id=row.id,
        learner_id=row.learner_id,
        attempt_id=row.attempt_id,
        course_id=row.course_id,
        question_id=row.question_id,
        status=CoachingSessionStatus(row.status),
        mode=CoachingMode(row.mode),
        started_at=to_iso(row.started_at),
        updated_at=to_iso(row.updated_at),
        topic=row.topic,
        question_position=row.question_position,
        exchange_count=row.exchange_count,
        direct_explanation_threshold=row.direct_explanation_threshold,
        direct_explanation_offered=bool(row.direct_explanation_offered),
        consecutive_failures=row.consecutive_failures,
        last_failure_code=row.last_failure_code,
        completed_at=to_iso(row.completed_at) if row.completed_at else None,
        revision=row.revision,
    )


def _instant(value: str) -> datetime:
    """A domain timestamp as the aware UTC datetime the column demands.

    ``UtcDateTime`` refuses a naive value outright, and the domain only ever produces ``…Z`` strings
    through ``app.core.time.to_iso``, so a failure here means something built a timestamp by hand.
    """
    return parse_instant(value)


def _to_message(row: CoachingMessageRow) -> ChatMessage:
    return ChatMessage(
        role=MessageRole(row.role),
        content=row.content,
        index=row.message_index,
        created_at=to_iso(row.created_at),
        mode=row.mode,
    )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SqlAlchemyCoachingSessionRepository:
    """``CoachingSessionRepository`` over ``qk_coaching_sessions``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get(self, session_id: str) -> CoachingSession | None:
        return await offload(self._get, session_id)

    async def get_for_learner(self, learner_id: str, session_id: str) -> CoachingSession | None:
        return await offload(self._get_for_learner, learner_id, session_id)

    async def find_open(
        self, learner_id: str, attempt_id: str, question_id: str
    ) -> CoachingSession | None:
        return await offload(self._find_open, learner_id, attempt_id, question_id)

    async def list_for_attempt(
        self, learner_id: str, attempt_id: str
    ) -> tuple[CoachingSession, ...]:
        return await offload(self._list_for_attempt, learner_id, attempt_id)

    async def insert(self, session: CoachingSession) -> CoachingSession:
        return await offload(self._insert, session)

    async def update(self, session: CoachingSession) -> CoachingSession:
        return await offload(self._update, session)

    # -- synchronous bodies -------------------------------------------------

    def _get(self, session_id: str) -> CoachingSession | None:
        row = self._session.get(CoachingSessionRow, session_id)
        return _to_session(row) if row is not None else None

    def _get_for_learner(self, learner_id: str, session_id: str) -> CoachingSession | None:
        # Ownership is part of the query, not a check the caller could forget after the fact (§9).
        row = self._session.scalar(
            select(CoachingSessionRow).where(
                CoachingSessionRow.id == session_id,
                CoachingSessionRow.learner_id == str(learner_id),
            )
        )
        return _to_session(row) if row is not None else None

    def _find_open(
        self, learner_id: str, attempt_id: str, question_id: str
    ) -> CoachingSession | None:
        row = self._session.scalar(
            select(CoachingSessionRow).where(
                CoachingSessionRow.learner_id == str(learner_id),
                CoachingSessionRow.attempt_id == attempt_id,
                CoachingSessionRow.question_id == question_id,
            )
        )
        return _to_session(row) if row is not None else None

    def _list_for_attempt(
        self, learner_id: str, attempt_id: str
    ) -> tuple[CoachingSession, ...]:
        rows = self._session.scalars(
            select(CoachingSessionRow)
            .where(
                CoachingSessionRow.learner_id == str(learner_id),
                CoachingSessionRow.attempt_id == attempt_id,
            )
            # Delivery position, then question id for anything unpositioned — the same deterministic
            # order the in-memory implementation produces, so the review queue reads identically.
            .order_by(CoachingSessionRow.question_position, CoachingSessionRow.question_id)
        ).all()
        return tuple(_to_session(row) for row in rows)

    def _insert(self, session: CoachingSession) -> CoachingSession:
        row = CoachingSessionRow(
            id=session.session_id,
            learner_id=session.learner_id,
            attempt_id=session.attempt_id,
            course_id=session.course_id,
            question_id=session.question_id,
            question_position=session.question_position,
            topic=session.topic,
            status=session.status.value,
            mode=session.mode.value,
            exchange_count=session.exchange_count,
            direct_explanation_threshold=session.direct_explanation_threshold,
            direct_explanation_offered=session.direct_explanation_offered,
            consecutive_failures=session.consecutive_failures,
            last_failure_code=session.last_failure_code,
            revision=session.revision,
            started_at=_instant(session.started_at),
            updated_at=_instant(session.updated_at),
            completed_at=_instant(session.completed_at) if session.completed_at else None,
        )
        self._session.add(row)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            # The unique constraint on the natural key decided a race. The caller reads the winner
            # rather than overwriting it, which is what makes starting coaching idempotent (§30).
            raise DuplicateCoachingSessionError(*session.natural_key) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("coaching.session.insert", exc) from exc
        return session

    def _update(self, session: CoachingSession) -> CoachingSession:
        row = self._session.get(CoachingSessionRow, session.session_id)
        if row is None:
            raise CoachingSessionNotFoundError(session.session_id)
        if (row.learner_id, row.attempt_id, row.question_id) != session.natural_key:
            # A session that moved to a different question would be a different conversation
            # wearing the same id. Refused here as the unique constraint would refuse it.
            raise DuplicateCoachingSessionError(*session.natural_key)

        row.status = session.status.value
        row.mode = session.mode.value
        row.topic = session.topic
        row.question_position = session.question_position
        row.exchange_count = session.exchange_count
        row.direct_explanation_threshold = session.direct_explanation_threshold
        row.direct_explanation_offered = session.direct_explanation_offered
        row.consecutive_failures = session.consecutive_failures
        row.last_failure_code = session.last_failure_code
        row.revision = session.revision
        row.updated_at = _instant(session.updated_at)
        row.completed_at = _instant(session.completed_at) if session.completed_at else None
        try:
            self._session.commit()
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("coaching.session.update", exc) from exc
        return session


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------


class SqlAlchemyCoachingTranscriptRepository:
    """``CoachingTranscriptRepository`` over ``qk_coaching_messages``.

    Append-only in the contract, and append-only in the database: ``trg_qk_message_no_update``
    rejects every ``UPDATE``, so "nothing rewrites what a learner said" holds even against raw SQL.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get(self, session_id: str) -> CoachingTranscript:
        return await offload(self._get, session_id)

    async def append(self, session_id: str, *messages: ChatMessage) -> CoachingTranscript:
        return await offload(self._append, session_id, messages)

    # -- synchronous bodies -------------------------------------------------

    def _get(self, session_id: str) -> CoachingTranscript:
        rows = self._session.scalars(
            select(CoachingMessageRow)
            .where(CoachingMessageRow.session_id == session_id)
            .order_by(CoachingMessageRow.message_index)
        ).all()
        # An empty transcript, not None: a session with no conversation yet is a normal state.
        return CoachingTranscript(
            session_id=session_id, messages=tuple(_to_message(row) for row in rows)
        )

    def _append(
        self, session_id: str, messages: tuple[ChatMessage, ...]
    ) -> CoachingTranscript:
        for message in messages:
            self._session.add(
                CoachingMessageRow(
                    session_id=session_id,
                    message_index=message.index,
                    role=message.role.value,
                    content=message.content,
                    mode=message.mode,
                    created_at=_instant(message.created_at),
                )
            )
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            # A duplicate (session_id, message_index) means two writers assigned the same slot.
            # Retryable: the caller re-reads the transcript and takes the next free index.
            raise PersistenceFailedError("coaching.transcript.append", exc) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("coaching.transcript.append", exc) from exc
        return self._get(session_id)


# ---------------------------------------------------------------------------
# The two outbound streams
# ---------------------------------------------------------------------------


class SqlAlchemyKnowledgeGapTracker:
    """``KnowledgeGapTracker`` over ``qk_knowledge_gaps``.

    Idempotent on ``session_id``, as the port asks: a duplicate write is swallowed rather than
    double-counting a topic.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def record_gap(self, event: KnowledgeGapEvent) -> None:
        await offload(self._record, event)

    def _record(self, event: KnowledgeGapEvent) -> None:
        now = utcnow()
        self._session.add(
            KnowledgeGapRow(
                session_id=event.session_id,
                learner_id=event.learner_id,
                attempt_id=event.attempt_id,
                course_id=event.course_id,
                question_id=event.question_id,
                topic=event.topic,
                source=event.source,
                occurred_at=_instant(event.occurred_at),
                created_at=now,
            )
        )
        try:
            self._session.commit()
        except IntegrityError:
            # Already recorded for this session. The unique constraint is the idempotency, and
            # arriving here is the normal outcome of a retry rather than a fault.
            self._session.rollback()
        except SQLAlchemyError as exc:
            self._session.rollback()
            # Analytics must never break coaching (§21). The caller isolates this too; logging here
            # means the operational record says which write was lost.
            logger.warning(
                "coaching.knowledge_gap_write_failed",
                extra={"session_id": event.session_id, "cause": type(exc).__name__},
            )


class SqlAlchemyCoachingActivityLog:
    """``CoachingActivityLog`` over ``qk_coaching_activity``.

    Append-only, by ``trg_qk_activity_no_update``: an audit record that could be edited is not an
    audit record.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def record(self, event: CoachingActivityEvent) -> None:
        await offload(self._record, event)

    def _record(self, event: CoachingActivityEvent) -> None:
        self._session.add(
            CoachingActivityRow(
                event_type=event.event_type.value,
                session_id=event.session_id,
                learner_id=event.learner_id,
                attempt_id=event.attempt_id,
                question_id=event.question_id,
                course_id=event.course_id,
                topic=event.topic,
                mode=event.mode,
                exchange_count=event.exchange_count,
                status=event.status,
                failure_code=event.failure_code,
                occurred_at=_instant(event.occurred_at),
                created_at=utcnow(),
            )
        )
        try:
            self._session.commit()
        except SQLAlchemyError as exc:
            self._session.rollback()
            # Activity logging must never break coaching (§22).
            logger.warning(
                "coaching.activity_write_failed",
                extra={"session_id": event.session_id, "cause": type(exc).__name__},
            )
