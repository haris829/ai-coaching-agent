"""The coaching session (§17, §15, §28, §30).

An immutable value object. Every transition returns a new session, so a service can compute the
next state, hand it to a repository, and never be left holding a half-mutated object if the write
fails.

IDENTITY (§30)
--------------
``session_id`` is generated, but it is not what makes a session unique. The natural key is
``(learner_id, attempt_id, question_id)`` — one coaching session per learner per incorrect
question — and that is what a repository enforces. Starting coaching twice therefore *resumes*
rather than opening a second conversation, however many times a client taps the button.

THE EXCHANGE COUNT (§15)
------------------------
An **exchange** is one learner message answered by one coach reply. Both halves must complete: an
AI failure mid-exchange leaves the count untouched, because a learner who typed into the void has
not been coached and must not be pushed closer to the direct-explanation threshold by an outage
(§27, §28).

The coach's opening question is not an exchange. It is the coach starting the conversation, with no
learner reasoning in it yet, and counting it would give away one of the five for free.

``direct_explanation_threshold`` is copied onto the session at creation rather than read from
configuration on each request, so changing the setting cannot move the goalposts under a
conversation that is already running.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.modules.coaching.domain.enums import CoachingMode, CoachingSessionStatus


@dataclass(frozen=True, slots=True)
class CoachingSession:
    """One learner's coaching conversation about one incorrectly answered question."""

    session_id: str
    learner_id: str
    attempt_id: str
    course_id: str
    question_id: str
    status: CoachingSessionStatus
    mode: CoachingMode
    started_at: str
    updated_at: str

    topic: str | None = None
    #: 1-based position of the question in the delivered paper, so a review queue can order
    #: sessions without re-reading UC-03.
    question_position: int | None = None
    exchange_count: int = 0
    #: The threshold in force for *this* session — see the module docstring.
    direct_explanation_threshold: int = 5
    #: True once the learner has been told the choice is available. Recorded so the offer is made
    #: once rather than repeated at every turn.
    direct_explanation_offered: bool = False
    #: Consecutive AI failures. Reset by any successful exchange; a run of them parks the session.
    consecutive_failures: int = 0
    #: The last failure's error code. Operational only — never a provider message (§29).
    last_failure_code: str | None = None
    completed_at: str | None = None
    #: Incremented on every stored transition, for optimistic concurrency in a real repository.
    revision: int = 1

    # ---- Queries ----------------------------------------------------------

    @property
    def natural_key(self) -> tuple[str, str, str]:
        return (self.learner_id, self.attempt_id, self.question_id)

    @property
    def direct_explanation_available(self) -> bool:
        """§15's transition: after five exchanges the learner may choose (§16)."""
        return self.exchange_count >= self.direct_explanation_threshold

    @property
    def is_live(self) -> bool:
        return self.status is CoachingSessionStatus.ACTIVE

    @property
    def is_terminal(self) -> bool:
        return self.status is CoachingSessionStatus.COMPLETED

    def exchanges_until_choice(self) -> int:
        """How many more exchanges before the direct-explanation choice is offered."""
        return max(0, self.direct_explanation_threshold - self.exchange_count)

    # ---- Transitions ------------------------------------------------------

    def touched(self, now: str) -> CoachingSession:
        return replace(self, updated_at=now, revision=self.revision + 1)

    def activated(self, now: str) -> CoachingSession:
        """Move the session to ACTIVE, from wherever it was (§28).

        Used when the coach speaks for the first time (UNAVAILABLE → ACTIVE), when a retry
        recovers a FAILED session, and when a learner returns to a question they had finished
        with. The last case clears ``completed_at``: reopening the same conversation is the whole
        point of the natural key, and a session that is ACTIVE while claiming to have completed
        would be a lie in every report built on top of it (§30).
        """
        return replace(
            self,
            status=CoachingSessionStatus.ACTIVE,
            consecutive_failures=0,
            last_failure_code=None,
            completed_at=None,
            updated_at=now,
            revision=self.revision + 1,
        )

    def with_exchange(self, now: str) -> CoachingSession:
        """Count one completed exchange: learner asked, coach answered (§15)."""
        count = self.exchange_count + 1
        return replace(
            self,
            exchange_count=count,
            status=CoachingSessionStatus.ACTIVE,
            consecutive_failures=0,
            last_failure_code=None,
            direct_explanation_offered=(
                self.direct_explanation_offered or count >= self.direct_explanation_threshold
            ),
            updated_at=now,
            revision=self.revision + 1,
        )

    def with_mode(self, mode: CoachingMode, now: str) -> CoachingSession:
        """Switch teaching mode. The *permission* to switch is a service rule (§16), not this."""
        return replace(self, mode=mode, updated_at=now, revision=self.revision + 1)

    def with_failure(self, code: str, now: str, *, limit: int) -> CoachingSession:
        """Record an AI failure.

        Below ``limit`` the session stays where it is and the exchange simply did not happen; at
        the limit it is parked as FAILED so a client stops retrying blindly and a human-visible
        state exists. Either way ``exchange_count`` is untouched (§28).
        """
        failures = self.consecutive_failures + 1
        status = self.status
        if failures >= limit and status is not CoachingSessionStatus.COMPLETED:
            status = CoachingSessionStatus.FAILED
        return replace(
            self,
            status=status,
            consecutive_failures=failures,
            last_failure_code=code,
            updated_at=now,
            revision=self.revision + 1,
        )

    def completed(self, now: str) -> CoachingSession:
        return replace(
            self,
            status=CoachingSessionStatus.COMPLETED,
            completed_at=now,
            updated_at=now,
            revision=self.revision + 1,
        )

    # ---- Serialisation ----------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """The state a frontend needs to render the conversation and its choices (§15, §17).

        ``direct_explanation_available`` is exposed as a plain boolean because that is the shape a
        frontend consumes — UC-07 states the fact and builds no UI for it (§4).
        """
        return {
            "session_id": self.session_id,
            "learner_id": self.learner_id,
            "attempt_id": self.attempt_id,
            "course_id": self.course_id,
            "question_id": self.question_id,
            "question_position": self.question_position,
            "topic": self.topic,
            "mode": self.mode.value,
            "status": self.status.value,
            "exchange_count": self.exchange_count,
            "direct_explanation_available": self.direct_explanation_available,
            "direct_explanation_offered": self.direct_explanation_offered,
            "direct_explanation_threshold": self.direct_explanation_threshold,
            "exchanges_until_choice": self.exchanges_until_choice(),
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "last_failure_code": self.last_failure_code,
            "revision": self.revision,
        }


def new_session(
    *,
    session_id: str,
    learner_id: str,
    attempt_id: str,
    course_id: str,
    question_id: str,
    now: str,
    topic: str | None = None,
    question_position: int | None = None,
    direct_explanation_threshold: int = 5,
) -> CoachingSession:
    """A fresh session.

    It starts ``UNAVAILABLE`` rather than ``ACTIVE``, and that is the point: the record is written
    *before* the model is called, so a failure to produce the opening question leaves one
    recoverable
    session instead of either a lost conversation or a duplicate one (§28, §30). The first coach
    turn moves it to ACTIVE.

    It also starts in ``SOCRATIC``. There is no code path that creates a session in
    ``DIRECT_EXPLANATION`` — that mode is reachable only through the five-exchange transition
    (§15, §16).
    """
    return CoachingSession(
        session_id=session_id,
        learner_id=learner_id,
        attempt_id=attempt_id,
        course_id=course_id,
        question_id=question_id,
        status=CoachingSessionStatus.UNAVAILABLE,
        mode=CoachingMode.SOCRATIC,
        started_at=now,
        updated_at=now,
        topic=topic,
        question_position=question_position,
        direct_explanation_threshold=direct_explanation_threshold,
    )
