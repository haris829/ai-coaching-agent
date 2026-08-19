"""Persistence contracts (§5, §18, §30, §32).

UC-07 was built with **no schema**: these protocols were satisfied by dictionaries, because the
company database did not exist yet and inventing one would have been guesswork. In the merged system
it does, so they have real implementations — ``sqlalchemy.py`` over the ``qk_`` tables — and the
in-memory ones in ``in_memory.py`` remain the default the coaching tests run against.

Not one line of this file changed to make that happen, which is the point of having written it. What
it defines is the set of guarantees an implementation must provide, because the module's correctness
rests on them:

========================  ===========================  ===================================
Concern                   Mechanism                    Why
========================  ===========================  ===================================
Idempotent start             ``(learner_id, attempt_id,       Repeated "start coaching" resumes one
                             question_id)`` is UNIQUE          session instead of opening a second
                             (enforced in the database —       (§30).
                             see ``coaching/models.py``)
Stable identity              ``session_id`` never changes      A frontend holding a session id keeps
                             on ``update``                     working across retries (§28).
Ownership-scoped reads       ``get_for_learner``,              A learner cannot read another
                             ``list_for_attempt``              learner's coaching session (§9).
Ordered conversation         ``ChatMessage.index`` is          Replay order never depends on clock
                             assigned by the service           skew in a distributed store (§18).
========================  ===========================  ===================================

WHERE THE OTHER §32 PORTS LIVE
------------------------------
§32 also names a coaching-activity port and a knowledge-gap port. Those are *outbound streams* —
UC-07 writes events and never reads them back — so they live with the other outbound integrations
rather than here:

* ``CoachingActivityLog``  → ``app.modules.coaching.integration.activity``
* ``KnowledgeGapTracker``  → ``app.modules.coaching.integration.knowledge_gaps``
* ``AttemptProvider``      → ``…integration.uc03``
* ``FeedbackProvider``     → ``…integration.uc06``
* ``CoachingLLM``          → ``…integration.llm``

Splitting them that way keeps one useful property: everything in *this* file is state UC-07 reads
back and must therefore reason about consistency for, and everything in ``integration`` is not.

TWO DELIBERATE ABSENCES
-----------------------
**No delete.** Neither a session nor a transcript can be removed through these contracts. Erasure
(a data-subject request, a retention policy) is a separate, audited business capability with its
own contract; it will not be built on top of this one by accident.

**No transcript search or listing.** A transcript is readable only by its ``session_id``, which the
ownership checks have already established belongs to the requesting learner. There is deliberately
no "all conversations for a course" read, because nothing in UC-07 needs one and the existence of
such a method is how a coaching corpus quietly becomes a dataset (§18, §22).

Implementations should raise ``app.core.errors.PersistenceFailedError`` for transient persistence
faults and ``DuplicateCoachingSessionError`` when the uniqueness constraint is violated. That last
one is not a condition to be avoided — the service catches it and reads the winner, which is how two
concurrent "start coaching" requests converge on one session (§30).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.modules.coaching.domain.session import CoachingSession
from app.modules.coaching.domain.transcript import ChatMessage, CoachingTranscript


@runtime_checkable
class CoachingSessionRepository(Protocol):
    """Storage for coaching sessions, unique on ``(learner_id, attempt_id, question_id)``."""

    async def get(self, session_id: str) -> CoachingSession | None:
        """The session with this id, or ``None``."""
        ...

    async def get_for_learner(self, learner_id: str, session_id: str) -> CoachingSession | None:
        """The session **only if it belongs to this learner** (§9).

        Not a convenience wrapper: it is the ownership-scoped read every learner-facing path uses,
        so a guessed session id returns ``None`` rather than someone else's conversation.
        Implementations must filter on ``learner_id`` in the query, not after it.
        """
        ...

    async def find_open(
        self, learner_id: str, attempt_id: str, question_id: str
    ) -> CoachingSession | None:
        """The existing session for this natural key, in any status, or ``None``.

        This is the lookup that makes starting coaching idempotent (§30).
        """
        ...

    async def list_for_attempt(
        self, learner_id: str, attempt_id: str
    ) -> tuple[CoachingSession, ...]:
        """Every session this learner has on this attempt.

        Feeds the review queue, which derives its progress from the sessions that exist rather
        than from stored progress of its own (§19).
        """
        ...

    async def insert(self, session: CoachingSession) -> CoachingSession:
        """Store a session for a natural key that does not have one.

        Raises ``DuplicateCoachingSessionError`` when it does. Callers treat that as "a concurrent
        request opened it first" and read the winner rather than overwriting (§30).
        """
        ...

    async def update(self, session: CoachingSession) -> CoachingSession:
        """Replace an existing session, keeping ``session_id`` and the natural key unchanged.

        Implementations must refuse when no session exists (``CoachingSessionNotFoundError``) and
        must not allow either identity to change — a session that moved to a different question
        would be a different conversation wearing the same id.
        """
        ...


@runtime_checkable
class CoachingTranscriptRepository(Protocol):
    """Conversation state for a coaching session (§18).

    Separate from the session repository because the two have genuinely different characters: a
    session is small, frequently updated state that many things read, and a transcript is an
    append-only body of learner content that only its own session reads. A real deployment will
    very likely want different storage, retention and access rules for each — splitting the
    contracts now means that costs one binding rather than a refactor.
    """

    async def get(self, session_id: str) -> CoachingTranscript:
        """The stored conversation. Returns an empty transcript when nothing is stored yet."""
        ...

    async def append(self, session_id: str, *messages: ChatMessage) -> CoachingTranscript:
        """Append turns and return the updated transcript.

        Append-only: there is no method to edit or remove a message. Rewriting what a learner said
        is not a capability this module should have.
        """
        ...
