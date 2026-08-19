"""Coaching activity logging (§22).

An operational record of coaching sessions: that one started, how it progressed, how it ended.
§22 lists exactly what to track — session, attempt, learner, question, topic, mode, exchange count,
started/completed state, timestamp — and, more importantly, what not to:

* no answer keys,
* no hidden correct answers,
* no sensitive learner information,
* no full conversation.

``CoachingActivityEvent`` has no field capable of holding any of those, which is a stronger
guarantee than a rule saying not to pass them. The conversation is *state*, kept in the transcript
repository because the next request needs it (§18); it is not activity, and it does not belong in an
activity stream that will be fanned out to dashboards and warehouses.

**Activity logging can never affect a coaching session.** Every call is isolated by the caller: a
failing sink produces a log line and is dropped. A learner does not lose their session because an
analytics pipeline is down.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)


class CoachingActivityType(StrEnum):
    """The lifecycle moments worth recording (§22).

    Deliberately coarse. One event per exchange is enough to answer "how much coaching is
    happening, on which topics, and does it finish?"; anything finer starts to describe the
    conversation itself.
    """

    SESSION_STARTED = "SESSION_STARTED"
    #: One learner message answered by one coach reply.
    EXCHANGE_COMPLETED = "EXCHANGE_COMPLETED"
    #: The learner was offered the Socratic / direct-explanation choice (§15).
    DIRECT_EXPLANATION_OFFERED = "DIRECT_EXPLANATION_OFFERED"
    MODE_CHANGED = "MODE_CHANGED"
    SESSION_COMPLETED = "SESSION_COMPLETED"
    #: The AI service failed. Recorded because an outage's shape matters operationally.
    SESSION_FAILED = "SESSION_FAILED"
    #: A failed or stalled session was resumed (§28).
    SESSION_RETRIED = "SESSION_RETRIED"


@dataclass(frozen=True, slots=True)
class CoachingActivityEvent:
    """One coaching lifecycle event. Identifiers, counts and codes only."""

    event_type: CoachingActivityType
    session_id: str
    attempt_id: str
    learner_id: str
    question_id: str
    occurred_at: str
    course_id: str | None = None
    topic: str | None = None
    mode: str | None = None
    exchange_count: int = 0
    status: str | None = None
    #: Error code for SESSION_FAILED. A code from UC-07's taxonomy — never a provider message,
    #: which can echo back the prompt it was given (§29).
    failure_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "session_id": self.session_id,
            "attempt_id": self.attempt_id,
            "learner_id": self.learner_id,
            "question_id": self.question_id,
            "course_id": self.course_id,
            "topic": self.topic,
            "mode": self.mode,
            "exchange_count": self.exchange_count,
            "status": self.status,
            "failure_code": self.failure_code,
            "occurred_at": self.occurred_at,
        }


@runtime_checkable
class CoachingActivityLog(Protocol):
    """Port onto the company's activity/audit pipeline (§22, §32).

    Satisfied today by ``qk_coaching_activity`` through
    ``repositories.sqlalchemy.SqlAlchemyCoachingActivityLog``, and still a port precisely so the
    company's own pipeline replaces it by changing the line that names it.
    """

    async def record(self, event: CoachingActivityEvent) -> None: ...


class LoggingCoachingActivityLog:
    """The shipped default: one structured log line per event."""

    async def record(self, event: CoachingActivityEvent) -> None:
        logger.info("coaching.activity", extra=event.as_dict())


class NullCoachingActivityLog:
    """Records nothing. For hosts that route activity elsewhere entirely."""

    async def record(self, event: CoachingActivityEvent) -> None:
        return None
