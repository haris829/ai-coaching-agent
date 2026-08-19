"""Review all wrong answers (§19, §20).

    Submitted attempt → find all incorrect questions → order them → coach through them in turn

THE QUEUE IS DERIVED, NOT STORED
--------------------------------
There is no review-progress table and no cursor to keep in sync. The queue is rebuilt from two
authoritative sources on every read:

* **which questions are in it** comes from UC-04's outcomes (§20). UC-07 has no rule of its own
  about what "incorrect" means, so the queue cannot drift from the score the learner was shown;
* **how far through it the learner is** comes from the coaching sessions that already exist. A
  question with a COMPLETED session is done; one with a live session is in progress; one with no
  session has not been started.

That makes "move to the next question" idempotent by construction, and makes the queue correct even
if a client abandons a review halfway through, reloads a day later, or runs two devices at once.
Stored progress could disagree with the sessions; derived progress cannot (§30).

ORDERING
--------
Delivery order — the order the learner sat the paper in — with the question id as a tiebreak so a
missing or duplicated position cannot make the sequence unstable between requests.

WHAT NEVER ENTERS THE QUEUE (§20)
---------------------------------
Correct questions, unanswered questions (unless UC-04 itself calls them incorrect), questions from
another attempt, and questions belonging to another learner. The first two are decided by
``QuestionResult.coachable``; the last two never get this far, because the caller has already
passed the ownership and attempt checks in ``eligibility``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.modules.coaching.domain.enums import CoachingSessionStatus, ReviewItemStatus
from app.modules.coaching.domain.session import CoachingSession
from app.modules.coaching.domain.topics import primary_topic
from app.modules.coaching.integration.uc03 import DeliveredQuestion
from app.modules.coaching.integration.uc04 import AttemptScore
from app.modules.coaching.integration.uc06 import AttemptFeedback


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """One incorrectly answered question in the review queue."""

    question_id: str
    position: int
    status: ReviewItemStatus
    topic: str | None = None
    session_id: str | None = None
    exchange_count: int = 0

    @property
    def pending(self) -> bool:
        return self.status is ReviewItemStatus.PENDING

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "position": self.position,
            "status": self.status.value,
            "topic": self.topic,
            "session_id": self.session_id,
            "exchange_count": self.exchange_count,
            # The per-question flag a frontend reads to decide whether to offer the action (§4,
            # §10). Every item in this queue is by definition an incorrect question the learner
            # owns, so coaching is available for all of them until they are finished with it.
            "coaching_available": self.status is not ReviewItemStatus.COMPLETED,
        }


@dataclass(frozen=True, slots=True)
class ReviewQueue:
    """Every incorrect question for one attempt, in the order they were delivered."""

    attempt_id: str
    learner_id: str
    items: tuple[ReviewItem, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def completed_count(self) -> int:
        return sum(1 for item in self.items if item.status is ReviewItemStatus.COMPLETED)

    @property
    def remaining_count(self) -> int:
        return self.total - self.completed_count

    @property
    def finished(self) -> bool:
        return self.total > 0 and self.remaining_count == 0

    def current(self) -> ReviewItem | None:
        """The question a coaching session is already open on, if any."""
        return next(
            (item for item in self.items if item.status is ReviewItemStatus.IN_PROGRESS), None
        )

    def next_pending(self) -> ReviewItem | None:
        """The first question not yet started."""
        return next((item for item in self.items if item.pending), None)

    def next_item(self) -> ReviewItem | None:
        """What "coach me next" should return.

        An in-progress question wins over an unstarted one: a learner who steps away mid-question
        comes back to the conversation they were having, not to a new one (§19, §30).
        """
        return self.current() or self.next_pending()

    def item_for(self, question_id: str) -> ReviewItem | None:
        return next((item for item in self.items if item.question_id == question_id), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "total_incorrect": self.total,
            "completed_count": self.completed_count,
            "remaining_count": self.remaining_count,
            "finished": self.finished,
            "items": [item.as_dict() for item in self.items],
            "next_question_id": (self.next_item().question_id if self.next_item() else None),
        }


#: Session status → where the question sits in the queue. FAILED and UNAVAILABLE sessions count as
#: IN_PROGRESS on purpose: the learner started this question and an AI problem interrupted them, so
#: the review should return them to it rather than silently skipping past (§28).
_ITEM_STATUS_BY_SESSION: Mapping[CoachingSessionStatus, ReviewItemStatus] = {
    CoachingSessionStatus.ACTIVE: ReviewItemStatus.IN_PROGRESS,
    CoachingSessionStatus.UNAVAILABLE: ReviewItemStatus.IN_PROGRESS,
    CoachingSessionStatus.FAILED: ReviewItemStatus.IN_PROGRESS,
    CoachingSessionStatus.COMPLETED: ReviewItemStatus.COMPLETED,
}


def build_review_queue(
    *,
    learner_id: str,
    attempt_id: str,
    score: AttemptScore,
    questions: Sequence[DeliveredQuestion] = (),
    feedback: AttemptFeedback | None = None,
    sessions: Sequence[CoachingSession] = (),
) -> ReviewQueue:
    """Assemble the queue from the authoritative outcomes and the sessions that exist (§19, §20)."""
    delivered = {question.question_id: question for question in questions}
    session_by_question = {
        session.question_id: session
        for session in sessions
        if session.attempt_id == attempt_id and session.learner_id == learner_id
    }

    items: list[ReviewItem] = []
    for result in score.incorrect_results():
        session = session_by_question.get(result.question_id)
        items.append(
            ReviewItem(
                question_id=result.question_id,
                position=result.position,
                status=(
                    _ITEM_STATUS_BY_SESSION.get(session.status, ReviewItemStatus.IN_PROGRESS)
                    if session
                    else ReviewItemStatus.PENDING
                ),
                topic=primary_topic(
                    delivered.get(result.question_id),
                    feedback.feedback_for(result.question_id) if feedback else None,
                ),
                session_id=session.session_id if session else None,
                exchange_count=session.exchange_count if session else 0,
            )
        )

    return ReviewQueue(attempt_id=attempt_id, learner_id=learner_id, items=tuple(items))
