"""Persistence contracts (§16, §20).

**UC-09 defines no schema.** There is no ORM model, no migration, no table and no SQL anywhere in
this module — the company database will be connected to these protocols at integration time. What
this file defines is the set of guarantees the eventual implementation must provide, because the
correctness of the whole module rests on them:

==============================  ==========================================  =======================
Repository                       Unique on                                   Guarantees
==============================  ==========================================  =======================
``FormalAttemptRepository``      ``(learner_id, quiz_id)`` among open        Two concurrent starts
                                 states                                      cannot become two
                                                                             formal attempts.
``FormalAttemptRepository``      ``attempt_id`` where not null                One formal record per
                                                                             UC-03 attempt.
``DeviceSessionRepository``      ``formal_attempt_id`` among ACTIVE           Two devices cannot
both
                                 sessions                                    be authoritative (§3).
``FormalReviewRepository``       ``formal_attempt_id``                        One review — and so
one
                                                                             queue entry — per pass
                                                                             (§13, §20).
==============================  ==========================================  =======================

**Every one of those constraints must be enforced by the database, not by application code.** A
check that reads and then writes has a window between the two, and that window is exactly the race
§20 describes. In SQL each is a partial unique index::

    CREATE UNIQUE INDEX ux_formal_attempt_open
        ON formal_attempts (learner_id, quiz_id) WHERE state IN
        ('CONDITIONS_ACKNOWLEDGED','IDENTITY_CONFIRMED','ACTIVE',
                        'AUTO_SUBMIT_IN_PROGRESS');

    CREATE UNIQUE INDEX ux_formal_attempt_upstream
        ON formal_attempts (attempt_id) WHERE attempt_id IS NOT NULL;

    CREATE UNIQUE INDEX ux_device_session_active
        ON formal_device_sessions (formal_attempt_id) WHERE state = 'ACTIVE';

    CREATE UNIQUE INDEX ux_formal_review_attempt
        ON formal_reviews (formal_attempt_id);

The ``WHERE`` clauses are what let history accumulate: a learner may sit a formal assessment again,
a closed session may be followed by a new one, and neither needs a delete.

COMPARE-AND-SET ON EVERY UPDATE
-------------------------------
``save`` methods take the record with its ``version`` already incremented and must apply the write
only if the stored version is ``version - 1``, raising ``ConcurrentModificationError`` otherwise::

    UPDATE formal_attempts SET ..., version = :version
     WHERE id = :id AND version = :version - 1

That single line is what makes the duplicate-submission, duplicate-disconnect, duplicate-decision
and duplicate-certificate races in §20 resolve to one winner. Services catch the error, re-read, and
either replay the winner's outcome or refuse — they never retry blindly, because a blind retry is
how one operation becomes two.

TWO DELIBERATE ABSENCES
-----------------------
**No delete, anywhere.** Not a formal attempt, not a device session, not a review. "Which device sat
this assessment?", "was another one turned away?", "who approved this certificate?" must stay
answerable, and a rejected session or an escalated review is a record, not a row to remove.

**No write path onto anything UC-09 does not own.** These contracts cannot touch an attempt, an
answer, a score, a pass/fail result, a certificate or a coaching session — none of them is reachable
from here at all. The immutability of the learner's attempt is a property of what this file does not
contain.

Implementations should raise ``app.core.errors.PersistenceFailedError`` for transient persistence
faults, and the specific errors from ``domain.errors`` when a uniqueness constraint or a version
check fails. Those are not conditions to be avoided: the services catch them and read the winner,
which is how concurrent requests converge instead of colliding.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.modules.formal_assessment.domain.attempt import FormalAttempt
from app.modules.formal_assessment.domain.device import DeviceSession
from app.modules.formal_assessment.domain.review import FormalReview


@runtime_checkable
class FormalAttemptRepository(Protocol):
    """Storage for formal attempts."""

    async def get(self, formal_attempt_id: str) -> FormalAttempt | None: ...

    async def get_for_learner(
        self, learner_id: str, formal_attempt_id: str
    ) -> FormalAttempt | None:
        """The record **only if it belongs to this learner**.

        Not a convenience wrapper: it is the ownership-scoped read every learner-facing path uses,
        so a guessed id returns ``None`` rather than someone else's assessment. Implementations must
        filter on ``learner_id`` in the query, not after it.
        """
        ...

    async def get_by_attempt_id(self, attempt_id: str) -> FormalAttempt | None:
        """The formal record wrapping a UC-03 attempt, if there is one.

        The read behind the certificate gate and the coaching check, both of which are asked about
        an attempt rather than about a formal attempt. Must be indexed: it is on the hot path of
        every certificate decision.
        """
        ...

    async def find_open_for_quiz(self, learner_id: str, quiz_id: str) -> FormalAttempt | None:
        """The learner's open formal attempt at this quiz, if any.

        "Open" is ``domain.enums.OPEN_FORMAL_STATES``. This is the read that makes acknowledging the
        conditions twice converge on one record instead of creating two.
        """
        ...

    async def list_in_progress_for_learner(self, learner_id: str) -> tuple[FormalAttempt, ...]:
        """Every formal attempt of this learner's that is being sat right now.

        Drives the AI-coaching restriction (§7), which is learner-scoped rather than attempt-scoped:
        coaching is refused while *any* formal assessment of theirs is in progress, on any quiz.
        Must be indexed on ``(learner_id, state)`` — it is called on every coaching request.
        """
        ...

    async def list_for_learner(self, learner_id: str) -> tuple[FormalAttempt, ...]:
        """Every formal attempt for this learner, oldest first."""
        ...

    async def insert(self, formal_attempt: FormalAttempt) -> FormalAttempt:
        """Store a new formal attempt.

        Raises ``DuplicateFormalAttemptError`` when an open record already exists for ``(learner_id,
        quiz_id)``. Must come from a database constraint, not from a preceding read.
        """
        ...

    async def save(self, formal_attempt: FormalAttempt) -> FormalAttempt:
        """Persist a change with a compare-and-set on ``version``.

        Raises ``ConcurrentModificationError`` when the stored version is not ``version - 1``, and
        ``FormalAttemptNotFoundError`` when the record does not exist — it must refuse to create
        one.

        Must not allow ``formal_attempt_id``, ``learner_id``, ``quiz_id`` or ``course_id`` to
        change: a record that moved to a different learner would be a different assessment wearing
        the same id. Must enforce ``attempt_id`` uniqueness on the transition that first sets it.
        """
        ...


@runtime_checkable
class DeviceSessionRepository(Protocol):
    """Storage for device sessions — the single-device lock (§3)."""

    async def get(self, session_id: str) -> DeviceSession | None: ...

    async def get_active(self, formal_attempt_id: str) -> DeviceSession | None:
        """The authoritative session for a formal attempt, or ``None``."""
        ...

    async def find_by_client_request_id(
        self, formal_attempt_id: str, client_request_id: str
    ) -> DeviceSession | None:
        """The session a retried registration should replay.

        Scoped to the formal attempt so a client reusing a token across assessments cannot have one
        replay the other's session. Returns any state: a retry that finds a *closed* session must be
        told the assessment is over, not handed a new lock.
        """
        ...

    async def list_for_attempt(self, formal_attempt_id: str) -> tuple[DeviceSession, ...]:
        """Every session against this formal attempt, oldest first, including rejected ones.

        The rejected ones are the evidence an assessor reads (§10).
        """
        ...

    async def claim(self, session: DeviceSession) -> DeviceSession:
        """Insert an ACTIVE session, taking the lock.

        Raises ``DeviceSessionAlreadyHeldError`` when an ACTIVE session already exists for the
        formal attempt. **This must be a database constraint.** It is the entire single-device
        guarantee: two simultaneous registrations both reach this method, one insert wins, and the
        loser is refused. A service-level "is there an active session?" check before an insert would
        have a window between the read and the write, and that window is the race.
        """
        ...

    async def record_rejected(self, session: DeviceSession) -> DeviceSession:
        """Store a REJECTED session — a device that was turned away.

        Never raises on uniqueness: a rejected session takes no lock, so any number of them may
        exist for one formal attempt. It must be stored even when the rejection is being reported as
        an error, which is why it is a separate method rather than a flag on ``claim``.
        """
        ...

    async def save(self, session: DeviceSession) -> DeviceSession:
        """Persist a change with a compare-and-set on ``version``.

        Closing an ACTIVE session must release the lock, so that a *new* formal attempt by the same
        learner can be registered later. It must not allow a CLOSED, DISCONNECTED or REJECTED
        session to return to ACTIVE: reactivating a session is how a disconnected formal attempt
        would be resumed, and §4 says it never can be.
        """
        ...


@runtime_checkable
class FormalReviewRepository(Protocol):
    """Storage for human reviews and their queue state (§9, §13)."""

    async def get(self, review_id: str) -> FormalReview | None: ...

    async def get_by_formal_attempt(self, formal_attempt_id: str) -> FormalReview | None:
        """The review for a formal attempt, if one exists. The read that makes creation idempotent.
        """
        ...

    async def insert(self, review: FormalReview) -> FormalReview:
        """Store a new review.

        Raises ``DuplicateReviewError`` when one already exists for ``formal_attempt_id``. From a
        database constraint: it is what stops one pass producing two reviews, and therefore two
        queue entries, under concurrency (§20).
        """
        ...

    async def save(self, review: FormalReview) -> FormalReview:
        """Persist a change with a compare-and-set on ``version``.

        Raises ``ConcurrentModificationError`` on a version mismatch — the mechanism behind the
        assessor-decision race in §20: two simultaneous decisions both read version *n*, both write
        *n+1*, and exactly one succeeds. Must not allow ``formal_attempt_id`` or a written decision
        change: a decision is final.
        """
        ...

    async def list_pending(
        self,
        *,
        course_ids: tuple[str, ...] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[FormalReview, ...]:
        """Reviews awaiting an assessor, oldest first.

        ``course_ids`` is the assessor's authorised scope. ``None`` means unrestricted and is used
        only for a platform-wide assessor; an **empty tuple** must return nothing, because an
        assessor authorised for no courses has an empty queue rather than a full one.

        Oldest first because a queue that surfaced the newest pass first would let an old assessment
        wait indefinitely.
        """
        ...

    async def count_pending(self, *, course_ids: tuple[str, ...] | None = None) -> int:
        """How many reviews are awaiting an assessor, for the queue depth an operator watches."""
        ...

    async def list_unpublished(self, *, limit: int = 100) -> tuple[FormalReview, ...]:
        """Reviews the queue has not accepted yet, oldest first (§13).

        The recovery surface. A review appears here while its publish state is PENDING or FAILED,
        which is how a queue outage becomes a work list instead of a silent loss.
        """
        ...
