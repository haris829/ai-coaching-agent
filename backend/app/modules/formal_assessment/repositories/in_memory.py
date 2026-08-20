"""Provisional in-memory repositories.

**These are not a database.** There is no schema, no SQL, no ORM and no migration — just
dictionaries guarded by an ``asyncio.Lock``, satisfying the protocols in ``protocols.py`` so the
module can run and be tested end to end before the company database exists.

They live in ``app`` rather than in ``tests`` for one reason: they are the default binding in the
composition root, so a standalone deployment of UC-09 starts and serves requests. When the real
persistence adapter is written, the only change is one line per repository in ``container.py``; no
service, no domain rule and no test of the formal-assessment logic changes.

What they *do* implement faithfully is the part the correctness argument depends on:

* one open formal attempt per ``(learner_id, quiz_id)``;
* one formal record per UC-03 ``attempt_id``;
* one ACTIVE device session per formal attempt;
* one review per formal attempt;
* compare-and-set on ``version`` for every update;
* the absence of any delete, and of any write path onto an attempt, a score or a certificate.

The lock is held across the read-and-write of each mutating method, which is what makes the
constraints hold rather than merely usually hold. A real implementation must get the same guarantee
from the database — an application-level check followed by a write has a window between the two, and
that window is the race §20 describes.
"""

from __future__ import annotations

import asyncio

from app.modules.formal_assessment.domain.attempt import FormalAttempt
from app.modules.formal_assessment.domain.device import DeviceSession
from app.modules.formal_assessment.domain.enums import (
    IN_PROGRESS_FORMAL_STATES,
    OPEN_FORMAL_STATES,
    OPEN_REVIEW_STATES,
    RECOVERABLE_PUBLISH_STATES,
    DeviceSessionState,
)
from app.modules.formal_assessment.domain.errors import (
    ConcurrentModificationError,
    DeviceSessionAlreadyHeldError,
    DuplicateFormalAttemptError,
    DuplicateReviewError,
    FormalAttemptNotFoundError,
    FormalReviewNotFoundError,
)
from app.modules.formal_assessment.domain.review import FormalReview


def _check_version(
    stored_version: int, incoming_version: int, *, record: str, identifier: str
) -> None:
    """The compare-and-set every ``save`` performs.

    ``incoming_version`` is the version the caller produced, so the stored record must be exactly
    one behind it. Anything else means somebody else wrote in between.
    """
    if stored_version != incoming_version - 1:
        raise ConcurrentModificationError(record=record, identifier=identifier)


class InMemoryFormalAttemptRepository:
    """Formal attempts, unique on the open ``(learner, quiz)`` pair and on ``attempt_id``."""

    def __init__(self) -> None:
        self._by_id: dict[str, FormalAttempt] = {}
        #: (learner_id, quiz_id) -> formal_attempt_id, for open states only. The partial unique
        #: index.
        self._open: dict[tuple[str, str], str] = {}
        #: attempt_id -> formal_attempt_id. The second unique index.
        self._by_attempt: dict[str, str] = {}
        #: Insertion order per learner, so "oldest first" is well defined.
        self._order: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def get(self, formal_attempt_id: str) -> FormalAttempt | None:
        return self._by_id.get(formal_attempt_id)

    async def get_for_learner(
        self, learner_id: str, formal_attempt_id: str
    ) -> FormalAttempt | None:
        stored = self._by_id.get(formal_attempt_id)
        if stored is None or stored.learner_id != learner_id:
            return None
        return stored

    async def get_by_attempt_id(self, attempt_id: str) -> FormalAttempt | None:
        formal_attempt_id = self._by_attempt.get(attempt_id)
        return self._by_id.get(formal_attempt_id) if formal_attempt_id else None

    async def find_open_for_quiz(self, learner_id: str, quiz_id: str) -> FormalAttempt | None:
        formal_attempt_id = self._open.get((learner_id, quiz_id))
        return self._by_id.get(formal_attempt_id) if formal_attempt_id else None

    async def list_in_progress_for_learner(self, learner_id: str) -> tuple[FormalAttempt, ...]:
        return tuple(
            self._by_id[identifier]
            for identifier in self._order.get(learner_id, [])
            if self._by_id[identifier].state in IN_PROGRESS_FORMAL_STATES
        )

    async def list_for_learner(self, learner_id: str) -> tuple[FormalAttempt, ...]:
        return tuple(self._by_id[identifier] for identifier in self._order.get(learner_id, []))

    async def insert(self, formal_attempt: FormalAttempt) -> FormalAttempt:
        async with self._lock:
            key = (formal_attempt.learner_id, formal_attempt.quiz_id)
            existing = self._open.get(key)
            if existing is not None:
                raise DuplicateFormalAttemptError(
                    learner_id=formal_attempt.learner_id,
                    quiz_id=formal_attempt.quiz_id,
                    existing_id=existing,
                )
            self._by_id[formal_attempt.formal_attempt_id] = formal_attempt
            if formal_attempt.state in OPEN_FORMAL_STATES:
                self._open[key] = formal_attempt.formal_attempt_id
            if formal_attempt.attempt_id:
                self._by_attempt[formal_attempt.attempt_id] = formal_attempt.formal_attempt_id
            self._order.setdefault(formal_attempt.learner_id, []).append(
                formal_attempt.formal_attempt_id
            )
            return formal_attempt

    async def save(self, formal_attempt: FormalAttempt) -> FormalAttempt:
        async with self._lock:
            stored = self._by_id.get(formal_attempt.formal_attempt_id)
            if stored is None:
                raise FormalAttemptNotFoundError(formal_attempt.formal_attempt_id)
            _check_version(
                stored.version,
                formal_attempt.version,
                record="formal_attempt",
                identifier=formal_attempt.formal_attempt_id,
            )

            key = (formal_attempt.learner_id, formal_attempt.quiz_id)
            if formal_attempt.state in OPEN_FORMAL_STATES:
                holder = self._open.get(key)
                if holder is not None and holder != formal_attempt.formal_attempt_id:
                    raise DuplicateFormalAttemptError(
                        learner_id=formal_attempt.learner_id,
                        quiz_id=formal_attempt.quiz_id,
                        existing_id=holder,
                    )
                self._open[key] = formal_attempt.formal_attempt_id
            elif self._open.get(key) == formal_attempt.formal_attempt_id:
                # The record has left the open states: release the slot so the learner may sit a
                # formal assessment at this quiz again later (a retake, for instance).
                del self._open[key]

            if formal_attempt.attempt_id:
                holder = self._by_attempt.get(formal_attempt.attempt_id)
                if holder is not None and holder != formal_attempt.formal_attempt_id:
                    raise DuplicateFormalAttemptError(
                        learner_id=formal_attempt.learner_id,
                        quiz_id=formal_attempt.quiz_id,
                        existing_id=holder,
                    )
                self._by_attempt[formal_attempt.attempt_id] = formal_attempt.formal_attempt_id

            self._by_id[formal_attempt.formal_attempt_id] = formal_attempt
            return formal_attempt


class InMemoryDeviceSessionRepository:
    """Device sessions, unique on one ACTIVE session per formal attempt (§3)."""

    def __init__(self) -> None:
        self._by_id: dict[str, DeviceSession] = {}
        #: formal_attempt_id -> session_id, for ACTIVE sessions only. The lock itself.
        self._active: dict[str, str] = {}
        self._order: dict[str, list[str]] = {}
        #: (formal_attempt_id, client_request_id) -> session_id, for registration replay.
        self._by_request: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: str) -> DeviceSession | None:
        return self._by_id.get(session_id)

    async def get_active(self, formal_attempt_id: str) -> DeviceSession | None:
        session_id = self._active.get(formal_attempt_id)
        return self._by_id.get(session_id) if session_id else None

    async def find_by_client_request_id(
        self, formal_attempt_id: str, client_request_id: str
    ) -> DeviceSession | None:
        session_id = self._by_request.get((formal_attempt_id, client_request_id))
        return self._by_id.get(session_id) if session_id else None

    async def list_for_attempt(self, formal_attempt_id: str) -> tuple[DeviceSession, ...]:
        return tuple(
            self._by_id[session_id] for session_id in self._order.get(formal_attempt_id, [])
        )

    async def claim(self, session: DeviceSession) -> DeviceSession:
        async with self._lock:
            holder = self._active.get(session.formal_attempt_id)
            if holder is not None:
                raise DeviceSessionAlreadyHeldError(
                    formal_attempt_id=session.formal_attempt_id, active_session_id=holder
                )
            self._store(session)
            self._active[session.formal_attempt_id] = session.session_id
            return session

    async def record_rejected(self, session: DeviceSession) -> DeviceSession:
        async with self._lock:
            # No uniqueness check: a rejected session holds no lock, so any number may exist.
            self._store(session)
            return session

    async def save(self, session: DeviceSession) -> DeviceSession:
        async with self._lock:
            stored = self._by_id.get(session.session_id)
            if stored is None:
                raise FormalAttemptNotFoundError(session.formal_attempt_id)
            _check_version(
                stored.version,
                session.version,
                record="device_session",
                identifier=session.session_id,
            )
            if session.state is DeviceSessionState.ACTIVE and not stored.active:
                # Reactivating a closed session is how a disconnected formal attempt would be
                # resumed.
                raise DeviceSessionAlreadyHeldError(
                    formal_attempt_id=session.formal_attempt_id,
                    active_session_id=self._active.get(session.formal_attempt_id),
                )
            if session.active:
                self._active[session.formal_attempt_id] = session.session_id
            elif self._active.get(session.formal_attempt_id) == session.session_id:
                del self._active[session.formal_attempt_id]
            self._by_id[session.session_id] = session
            return session

    def _store(self, session: DeviceSession) -> None:
        """Write the record and both indexes under the caller's lock."""
        self._by_id[session.session_id] = session
        self._order.setdefault(session.formal_attempt_id, []).append(session.session_id)
        if session.client_request_id:
            self._by_request.setdefault(
                (session.formal_attempt_id, session.client_request_id), session.session_id
            )


class InMemoryFormalReviewRepository:
    """Reviews, unique on ``formal_attempt_id`` (§13, §20)."""

    def __init__(self) -> None:
        self._by_id: dict[str, FormalReview] = {}
        self._by_formal_attempt: dict[str, str] = {}
        #: Insertion order, so "oldest first" is well defined for the queue and the recovery list.
        self._order: list[str] = []
        self._lock = asyncio.Lock()

    async def get(self, review_id: str) -> FormalReview | None:
        return self._by_id.get(review_id)

    async def get_by_formal_attempt(self, formal_attempt_id: str) -> FormalReview | None:
        review_id = self._by_formal_attempt.get(formal_attempt_id)
        return self._by_id.get(review_id) if review_id else None

    async def insert(self, review: FormalReview) -> FormalReview:
        async with self._lock:
            existing = self._by_formal_attempt.get(review.formal_attempt_id)
            if existing is not None:
                raise DuplicateReviewError(
                    formal_attempt_id=review.formal_attempt_id, existing_review_id=existing
                )
            self._by_id[review.review_id] = review
            self._by_formal_attempt[review.formal_attempt_id] = review.review_id
            self._order.append(review.review_id)
            return review

    async def save(self, review: FormalReview) -> FormalReview:
        async with self._lock:
            stored = self._by_id.get(review.review_id)
            if stored is None:
                raise FormalReviewNotFoundError(review.review_id)
            _check_version(
                stored.version, review.version, record="formal_review", identifier=review.review_id
            )
            self._by_id[review.review_id] = review
            return review

    async def list_pending(
        self,
        *,
        course_ids: tuple[str, ...] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[FormalReview, ...]:
        selected = [
            self._by_id[review_id]
            for review_id in self._order
            if self._by_id[review_id].state in OPEN_REVIEW_STATES
            and (course_ids is None or self._by_id[review_id].course_id in course_ids)
        ]
        return tuple(selected[offset : offset + limit])

    async def count_pending(self, *, course_ids: tuple[str, ...] | None = None) -> int:
        return sum(
            1
            for review_id in self._order
            if self._by_id[review_id].state in OPEN_REVIEW_STATES
            and (course_ids is None or self._by_id[review_id].course_id in course_ids)
        )

    async def list_unpublished(self, *, limit: int = 100) -> tuple[FormalReview, ...]:
        selected = [
            self._by_id[review_id]
            for review_id in self._order
            if self._by_id[review_id].publish_state in RECOVERABLE_PUBLISH_STATES
        ]
        return tuple(selected[:limit])
