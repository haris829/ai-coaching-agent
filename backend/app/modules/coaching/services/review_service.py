"""Eligibility and the review-all-wrong-answers flow (§10, §19, §20, §31).

Two capabilities that a frontend needs before any conversation starts:

**"Can this learner be coached, and on what?"** ``check_eligibility`` answers it for the attempt and
for every question on it. This is the backend half of "Show Review with Larry" (§4, §10): the
service states ``coaching_available`` per question, and the frontend decides what to render. No
button, no screen, no markup is built here.

**"Take me through everything I got wrong."** ``get_review`` and ``next_question`` walk the learner
through their incorrect questions in delivery order, one at a time.

The queue itself is derived rather than stored — see ``domain.review`` for why that matters. This
service adds the reading, the gate, and the one state change the flow needs: finishing with the
current question before handing back the next one.

NEITHER METHOD REQUIRES THE AI TO BE UP
---------------------------------------
Both authorise with ``require_service=False``. A learner can see which questions they got wrong and
how far through the review they are during an AI outage; what they cannot do is open a conversation,
and that refusal happens at ``start_coaching`` where it belongs (§27). Reporting "you have no
incorrect questions" because a model was unreachable would be a lie about their quiz.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.coaching.domain.eligibility import Eligibility
from app.modules.coaching.domain.enums import EligibilityCode
from app.modules.coaching.domain.errors import NoIncorrectQuestionsError, ScoreNotConfirmedError
from app.modules.coaching.domain.review import ReviewItem, ReviewQueue, build_review_queue
from app.modules.coaching.integration.uc03 import AttemptProvider
from app.modules.coaching.repositories.protocols import CoachingSessionRepository
from app.modules.coaching.services.authorization import CoachingAuthorizer
from app.modules.coaching.services.coaching_service import CoachingService


@dataclass(frozen=True, slots=True)
class QuestionEligibility:
    """Whether coaching may be offered for one question (§10)."""

    question_id: str
    position: int
    outcome: str
    coaching_available: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "position": self.position,
            "outcome": self.outcome,
            "coaching_available": self.coaching_available,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class CoachingEligibility:
    """The attempt-level verdict, plus a per-question breakdown when there is one."""

    attempt_id: str
    eligibility: Eligibility
    questions: tuple[QuestionEligibility, ...] = ()

    @property
    def coaching_available(self) -> bool:
        return self.eligibility.coaching_available

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            **self.eligibility.as_dict(),
            "questions": [item.as_dict() for item in self.questions],
            "incorrect_question_count": sum(
                1 for item in self.questions if item.coaching_available
            ),
        }


@dataclass(frozen=True, slots=True)
class ReviewAdvance:
    """The result of moving through the review queue (§19)."""

    queue: ReviewQueue
    next_item: ReviewItem | None = None
    #: The question that was finished with on the way, if any.
    completed_question_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "completed_question_id": self.completed_question_id,
            "next_question": self.next_item.as_dict() if self.next_item else None,
            "review": self.queue.as_dict(),
        }


class CoachingReviewService:
    """Eligibility reads and the sequential review of every incorrect question."""

    def __init__(
        self,
        *,
        authorizer: CoachingAuthorizer,
        attempts: AttemptProvider,
        sessions: CoachingSessionRepository,
        coaching: CoachingService,
    ) -> None:
        self._authorizer = authorizer
        self._attempts = attempts
        self._sessions = sessions
        self._coaching = coaching

    async def check_eligibility(
        self, *, learner_id: str, attempt_id: str, question_id: str | None = None
    ) -> CoachingEligibility:
        """Report whether coaching is available, without raising (§10, §27).

        Refusals are data here, not errors: this is the call a frontend makes to decide whether to
        offer the action at all, and it should get an answer for an active attempt just as readily
        as for a submitted one.
        """
        gate = await self._authorizer.evaluate(
            learner_id=learner_id, attempt_id=attempt_id, question_id=question_id
        )

        # Asked about one question: that verdict is the whole answer.
        if question_id is not None:
            return CoachingEligibility(attempt_id=attempt_id, eligibility=gate.eligibility)

        questions: tuple[QuestionEligibility, ...] = ()
        if gate.allowed and gate.score is not None:
            questions = tuple(
                QuestionEligibility(
                    question_id=result.question_id,
                    position=result.position,
                    outcome=result.outcome.value,
                    coaching_available=result.coachable,
                    reason=(
                        EligibilityCode.ELIGIBLE.value
                        if result.coachable
                        else EligibilityCode.QUESTION_NOT_INCORRECT.value
                    ),
                )
                for result in sorted(
                    gate.score.question_results,
                    key=lambda item: (item.position, item.question_id),
                )
            )

        return CoachingEligibility(
            attempt_id=attempt_id, eligibility=gate.eligibility, questions=questions
        )

    async def get_review(self, *, learner_id: str, attempt_id: str) -> ReviewQueue:
        """Every incorrect question on the attempt, with its coaching progress (§19)."""
        return await self._queue(learner_id=learner_id, attempt_id=attempt_id)

    async def next_question(
        self, *, learner_id: str, attempt_id: str, complete_current: bool = True
    ) -> ReviewAdvance:
        """Finish with the current question and hand back the next one (§19).

        ``complete_current`` defaults to true because that is what "next" means to a learner
        working through their wrong answers. Passing false makes this a pure read — useful for a
        client that wants to know what is coming without committing to leaving the current
        question.

        Idempotent: calling it again after the last question returns the same finished queue with
        no next item, rather than wrapping around or erroring.
        """
        queue = await self._queue(learner_id=learner_id, attempt_id=attempt_id)
        if queue.total == 0:
            raise NoIncorrectQuestionsError(attempt_id)

        completed_question_id: str | None = None
        current = queue.current()
        if complete_current and current is not None and current.session_id:
            await self._coaching.complete_session(
                learner_id=learner_id, session_id=current.session_id
            )
            completed_question_id = current.question_id
            queue = await self._queue(learner_id=learner_id, attempt_id=attempt_id)

        return ReviewAdvance(
            queue=queue,
            next_item=queue.next_item(),
            completed_question_id=completed_question_id,
        )

    async def _queue(self, *, learner_id: str, attempt_id: str) -> ReviewQueue:
        gate = await self._authorizer.authorize(
            learner_id=learner_id, attempt_id=attempt_id, require_service=False
        )
        if gate.score is None:  # pragma: no cover - the gate refuses an unconfirmed score first
            raise ScoreNotConfirmedError(attempt_id, None)

        questions = await self._attempts.get_delivered_questions(attempt_id)
        sessions = await self._sessions.list_for_attempt(learner_id, attempt_id)
        return build_review_queue(
            learner_id=learner_id,
            attempt_id=attempt_id,
            score=gate.score,
            questions=questions,
            feedback=gate.feedback,
            sessions=sessions,
        )
