"""Provisional in-memory repositories.

**These are not a database.** There is no schema, no SQL, no ORM and no migration — just
dictionaries guarded by an ``asyncio.Lock``, satisfying the protocols in ``protocols.py`` so the
module can run and be tested end to end before the company database exists.

They live in ``app`` rather than in ``tests`` for one reason: they are the default binding in the
composition root, so a standalone deployment of UC-08 starts and serves requests. When the real
persistence adapter is written, the only change is one line per repository in ``container.py``;
no service, no domain rule and no test of the retake logic changes.

What they *do* implement faithfully is the part the correctness argument depends on:

* ``idempotency_key`` uniqueness on both stores;
* ``(learner_id, quiz_id, attempt_number)`` uniqueness across non-FAILED retake requests;
* the absence of any delete, and of any write path onto an attempt, a score or a result.

The lock is held across the read-and-write of each mutating method, which is what makes the
constraints hold rather than merely usually hold. A real implementation must get the same
guarantee from the database — an application-level check followed by a write has a window
between the two, and that window is the race §15 describes.
"""

from __future__ import annotations

import asyncio

from app.modules.retakes.domain.enums import RetakeRequestStatus
from app.modules.retakes.domain.errors import (
    AttemptSlotTakenError,
    DuplicateGrantError,
    DuplicateRetakeRequestError,
    GrantNotFoundError,
    RetakeRequestNotFoundError,
)
from app.modules.retakes.domain.grants import AdditionalAttemptGrant
from app.modules.retakes.domain.requests import RetakeRequest


class InMemoryRetakeRequestRepository:
    """Retake reservations, unique on the idempotency key and on the attempt slot."""

    def __init__(self) -> None:
        self._by_id: dict[str, RetakeRequest] = {}
        self._by_key: dict[str, str] = {}
        #: (learner_id, quiz_id, attempt_number) -> retake_id, for non-FAILED requests only.
        self._slots: dict[tuple[str, str, int], str] = {}
        #: Insertion order per learner+quiz, so "oldest first" is well defined.
        self._order: dict[tuple[str, str], list[str]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _slot_of(request: RetakeRequest) -> tuple[str, str, int]:
        return (request.learner_id, request.quiz_id, request.attempt_number)

    async def get(self, retake_id: str) -> RetakeRequest | None:
        return self._by_id.get(retake_id)

    async def get_by_idempotency_key(self, idempotency_key: str) -> RetakeRequest | None:
        retake_id = self._by_key.get(idempotency_key)
        return self._by_id.get(retake_id) if retake_id else None

    async def get_for_learner(self, learner_id: str, retake_id: str) -> RetakeRequest | None:
        stored = self._by_id.get(retake_id)
        if stored is None or stored.learner_id != learner_id:
            return None
        return stored

    async def reserve(self, request: RetakeRequest) -> RetakeRequest:
        async with self._lock:
            if request.idempotency_key in self._by_key:
                raise DuplicateRetakeRequestError(request.idempotency_key)
            slot = self._slot_of(request)
            if slot in self._slots:
                raise AttemptSlotTakenError(*slot)

            self._by_id[request.retake_id] = request
            self._by_key[request.idempotency_key] = request.retake_id
            self._slots[slot] = request.retake_id
            self._order.setdefault((request.learner_id, request.quiz_id), []).append(
                request.retake_id
            )
            return request

    async def save(self, request: RetakeRequest) -> RetakeRequest:
        async with self._lock:
            stored = self._by_id.get(request.retake_id)
            if stored is None:
                raise RetakeRequestNotFoundError(request.retake_id)

            slot = self._slot_of(request)
            if request.status is RetakeRequestStatus.FAILED:
                # Release the slot: nothing was created, so nothing is owed.
                if self._slots.get(slot) == request.retake_id:
                    del self._slots[slot]
            else:
                holder = self._slots.get(slot)
                if holder is not None and holder != request.retake_id:
                    raise AttemptSlotTakenError(*slot)
                self._slots[slot] = request.retake_id

            self._by_id[request.retake_id] = request
            return request

    async def count_active_reservations(self, learner_id: str, quiz_id: str) -> int:
        return sum(
            1
            for retake_id in self._order.get((learner_id, quiz_id), [])
            if self._by_id[retake_id].status is RetakeRequestStatus.RESERVED
        )

    async def list_for_learner_quiz(
        self, learner_id: str, quiz_id: str
    ) -> tuple[RetakeRequest, ...]:
        return tuple(
            self._by_id[retake_id] for retake_id in self._order.get((learner_id, quiz_id), [])
        )


class InMemoryGrantRepository:
    """Additional-attempt grants, unique on the idempotency key."""

    def __init__(self) -> None:
        self._by_id: dict[str, AdditionalAttemptGrant] = {}
        self._by_key: dict[str, str] = {}
        self._order: dict[tuple[str, str, str], list[str]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _scope_of(grant: AdditionalAttemptGrant) -> tuple[str, str, str]:
        return (grant.learner_id, grant.course_id, grant.quiz_id)

    async def get(self, grant_id: str) -> AdditionalAttemptGrant | None:
        return self._by_id.get(grant_id)

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> AdditionalAttemptGrant | None:
        grant_id = self._by_key.get(idempotency_key)
        return self._by_id.get(grant_id) if grant_id else None

    async def list_for_learner_quiz(
        self, learner_id: str, course_id: str, quiz_id: str
    ) -> tuple[AdditionalAttemptGrant, ...]:
        return tuple(
            self._by_id[grant_id]
            for grant_id in self._order.get((learner_id, course_id, quiz_id), [])
        )

    async def insert(self, grant: AdditionalAttemptGrant) -> AdditionalAttemptGrant:
        async with self._lock:
            if grant.idempotency_key in self._by_key:
                raise DuplicateGrantError(grant.idempotency_key)
            # Both indexes are written under the same lock as the record, so a grant is never
            # half-visible: either it is findable by every route or by none (§14).
            self._by_id[grant.grant_id] = grant
            self._by_key[grant.idempotency_key] = grant.grant_id
            self._order.setdefault(self._scope_of(grant), []).append(grant.grant_id)
            return grant

    async def save(self, grant: AdditionalAttemptGrant) -> AdditionalAttemptGrant:
        async with self._lock:
            stored = self._by_id.get(grant.grant_id)
            if stored is None:
                raise GrantNotFoundError(grant.grant_id)
            if (
                stored.additional_attempts != grant.additional_attempts
                or self._scope_of(stored) != self._scope_of(grant)
            ):
                # The protocol forbids it, so the provisional store refuses it too rather than
                # letting a defect pass here and fail against the real database.
                raise ValueError(
                    "A grant's scope and attempt count are immutable; only its status may change."
                )
            self._by_id[grant.grant_id] = grant
            return grant
