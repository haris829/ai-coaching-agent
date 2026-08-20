"""Persistence contracts (§18, §19).

**UC-08 defines no schema.** There is no ORM model, no migration, no table and no SQL anywhere in
this module — the company database will be connected to these protocols at integration time. What
this file defines is the set of guarantees the eventual implementation must provide, because the
correctness of the whole module rests on them:

=============================  =========================================  =======================
Repository                      Unique on                                  Guarantees
=============================  =========================================  =======================
``RetakeRequestRepository``     ``idempotency_key``                        A replayed retake
                                                                           request resolves to
                                                                           one attempt (§16).
``RetakeRequestRepository``     ``(learner_id, quiz_id, attempt_number)``  Two concurrent
                                *among non-FAILED rows*                    retakes cannot both
                                                                           take one slot (§15).
``GrantRepository``             ``idempotency_key``                        A retried grant does
                                                                           not grant twice (§14).
=============================  =========================================  =======================

**Both retake constraints must be enforced by the database, not by application code.** A check
that reads and then writes has a window between the two, and that window is exactly the race §15
describes. In SQL the second one is a partial unique index::

    CREATE UNIQUE INDEX ux_retake_attempt_slot
        ON retake_requests (learner_id, quiz_id, attempt_number)
        WHERE status <> 'FAILED';

The ``WHERE`` clause is what lets a failed retake be retried into the same slot without a delete.

TWO DELIBERATE ABSENCES
-----------------------
**No delete, anywhere.** Not for a retake request, not for a grant. "Who gave this learner a
fourth attempt, and when?" must stay answerable, and a revoked grant is a status transition that
keeps the record rather than a row that disappears.

**No write path onto anything a retake follows.** These contracts cannot touch an attempt, an
answer, a score, a pass/fail result, a feedback report or a coaching session — none of them is
reachable from here at all. §3's immutability is a property of what this file does not contain.

Implementations should raise ``app.core.errors.PersistenceFailedError`` for transient persistence
faults, and the specific errors from ``domain.errors`` when a uniqueness constraint is violated.
Those are
not conditions to be avoided: the services catch them and read the winner, which is how
concurrent requests converge instead of colliding.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.modules.retakes.domain.grants import AdditionalAttemptGrant
from app.modules.retakes.domain.requests import RetakeRequest


@runtime_checkable
class RetakeRequestRepository(Protocol):
    """Storage for retake reservations, unique on the idempotency key and the attempt slot."""

    async def get(self, retake_id: str) -> RetakeRequest | None: ...

    async def get_by_idempotency_key(self, idempotency_key: str) -> RetakeRequest | None:
        """The request for this key, in any status, or ``None``.

        The first lookup every retake makes, and the one that turns a client retry into a read.
        """
        ...

    async def get_for_learner(self, learner_id: str, retake_id: str) -> RetakeRequest | None:
        """The request **only if it belongs to this learner**.

        Not a convenience wrapper: it is the ownership-scoped read every learner-facing path
        uses, so a guessed retake id returns ``None`` rather than someone else's record.
        Implementations must filter on ``learner_id`` in the query, not after it.
        """
        ...

    async def reserve(self, request: RetakeRequest) -> RetakeRequest:
        """Insert a RESERVED request, taking the attempt slot.

        Raises ``DuplicateRetakeRequestError`` when the idempotency key exists, and
        ``AttemptSlotTakenError`` when a non-FAILED request already holds
        ``(learner_id, quiz_id, attempt_number)``. Both must come from database constraints.
        """
        ...

    async def save(self, request: RetakeRequest) -> RetakeRequest:
        """Persist a lifecycle transition — RESERVED → COMPLETED, RESERVED → FAILED, or a
        FAILED request reopened for a retry.

        Must refuse to create a record that does not already exist, and must not allow
        ``retake_id``, ``idempotency_key`` or ``previous_attempt_id`` to change: a request that
        moved to a different previous attempt would be a different retake wearing the same id.

        Reopening a FAILED request must re-acquire the attempt slot under the same constraint as
        ``reserve``, raising ``AttemptSlotTakenError`` if another request took it meanwhile.
        """
        ...

    async def count_active_reservations(self, learner_id: str, quiz_id: str) -> int:
        """RESERVED requests that UC-03 has not yet turned into attempts.

        Added to UC-03's used count so the window between reserving a slot and the attempt
        appearing upstream cannot be used to exceed the allowance. COMPLETED requests are
        excluded — UC-03 can see those attempts and is already counting them.
        """
        ...

    async def list_for_learner_quiz(
        self, learner_id: str, quiz_id: str
    ) -> tuple[RetakeRequest, ...]:
        """Every retake request for this learner and quiz, oldest first.

        Supplies the retake relationships attempt history renders (§10).
        """
        ...


@runtime_checkable
class GrantRepository(Protocol):
    """Storage for additional-attempt grants, unique on the idempotency key."""

    async def get(self, grant_id: str) -> AdditionalAttemptGrant | None: ...

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> AdditionalAttemptGrant | None: ...

    async def list_for_learner_quiz(
        self, learner_id: str, course_id: str, quiz_id: str
    ) -> tuple[AdditionalAttemptGrant, ...]:
        """Every grant for this learner on this course and quiz, in any status, oldest first.

        Scoped to all three ids on purpose: this is the read that decides how many extra attempts
        a learner has, and a query that dropped the course or the quiz would hand a learner an
        extra attempt at a quiz nobody granted them one for (§12).
        """
        ...

    async def insert(self, grant: AdditionalAttemptGrant) -> AdditionalAttemptGrant:
        """Store a grant. Raises ``DuplicateGrantError`` when the key exists.

        Must be atomic: a grant is either stored whole or not at all. A partially applied grant
        would leave a learner's entitlement in a state no rule in the system can explain (§14).
        """
        ...

    async def save(self, grant: AdditionalAttemptGrant) -> AdditionalAttemptGrant:
        """Persist a lifecycle transition — currently only ACTIVE → REVOKED.

        Must refuse to create a record that does not already exist, and must not allow
        ``additional_attempts``, ``learner_id``, ``course_id`` or ``quiz_id`` to change. The
        number of attempts a grant conferred is part of the audit trail, not editable state.
        """
        ...
