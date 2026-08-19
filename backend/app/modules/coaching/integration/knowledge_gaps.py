"""Knowledge-gap tracking (§21).

When a learner opens coaching on an incorrect question, the topic of that question is recorded as
a *potential* knowledge gap. "Potential" is doing real work in that sentence: one wrong answer is
weak evidence, and it is the business's analytics — not UC-07 — that decides when a pattern of them
means something. This module's job is to emit an honest, minimal event and get out of the way.

WHAT IS RECORDED
----------------
Learner, attempt, course, question, topic, coaching session, timestamp. Exactly §21's list.

WHAT IS NOT
-----------
The learner's answer, the question text, the conversation, and — needless to say — the answer key.
A knowledge-gap dataset is about *topics*, and a topic is a label, not content (§22).

WHEN IT IS RECORDED
-------------------
Once per coaching session, at the moment the session is created. Not on resume, not per exchange,
and not per message: a learner who spends twenty turns on one question has one gap in one topic,
and counting their persistence as twenty gaps would make the dataset actively misleading (§21's
"duplicate/duplicate-session behavior is safe").

RECORDING CAN NEVER BREAK COACHING
----------------------------------
The caller isolates every call to this port. A tracker that is down, slow or defective produces a
log line and nothing else — a learner does not lose their coaching session because an analytics
sink was unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class KnowledgeGapEvent:
    """One topic a learner may need to revisit (§21).

    ``topic`` is ``None`` when the question carries no topic in either UC-03 or UC-06. That is
    recorded as-is rather than filled with a placeholder: an untagged question is a content problem
    worth seeing in the data, and inventing "General" would hide it.
    """

    learner_id: str
    attempt_id: str
    course_id: str
    question_id: str
    session_id: str
    occurred_at: str
    topic: str | None = None
    #: What caused the record. Currently always the opening of a coaching session; kept explicit so
    #: a future source (a repeated failure across attempts, say) is distinguishable in the data.
    source: str = "COACHING_SESSION_STARTED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "attempt_id": self.attempt_id,
            "course_id": self.course_id,
            "question_id": self.question_id,
            "session_id": self.session_id,
            "topic": self.topic,
            "source": self.source,
            "occurred_at": self.occurred_at,
        }


@runtime_checkable
class KnowledgeGapTracker(Protocol):
    """Port onto the company's knowledge-gap store.

    Satisfied today by ``qk_knowledge_gaps`` through
    ``repositories.sqlalchemy.SqlAlchemyKnowledgeGapTracker``, and still a port so the company's own
    store replaces it by changing the line that names it. Implementations must be idempotent on
    ``session_id`` so a retried write cannot double-count a topic — there, a unique constraint.
    """

    async def record_gap(self, event: KnowledgeGapEvent) -> None: ...


class LoggingKnowledgeGapTracker:
    """The shipped default: emit one structured line per gap.

    Genuinely useful before the analytics store exists — the lines are JSON, carry no learner
    content, and can be shipped straight into a log pipeline.
    """

    async def record_gap(self, event: KnowledgeGapEvent) -> None:
        logger.info("coaching.knowledge_gap", extra=event.as_dict())


class NullKnowledgeGapTracker:
    """Records nothing. For hosts that collect knowledge gaps somewhere else entirely."""

    async def record_gap(self, event: KnowledgeGapEvent) -> None:
        return None
