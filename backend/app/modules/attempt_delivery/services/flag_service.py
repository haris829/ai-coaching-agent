"""Question flagging ("mark for review").

State is persisted per attempt question, so it survives refresh and reconnection
exactly like an answer does. Setting a flag is idempotent: the same request repeated
is accepted rather than treated as an error, which keeps a retrying client simple.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.time import Clock, iso_or_none, to_iso
from app.modules.attempt_delivery.domain import errors
from app.modules.attempt_delivery.ids import new_id
from app.modules.attempt_delivery.repositories.attempt_question_repository import (
    AttemptQuestionRepository,
)
from app.modules.attempt_delivery.repositories.attempt_repository import AttemptRepository
from app.modules.attempt_delivery.repositories.flag_repository import FlagRepository
from app.modules.attempt_delivery.services.attempt_access_service import AttemptAccessService
from app.modules.attempt_delivery.services.timing_service import TimingService


class FlagService:
    """Sets and reads question flag state."""

    __slots__ = (
        "_session",
        "_attempts",
        "_attempt_questions",
        "_flags",
        "_access",
        "_timing",
        "_clock",
    )

    def __init__(
        self,
        *,
        session: Session,
        attempts: AttemptRepository,
        attempt_questions: AttemptQuestionRepository,
        flags: FlagRepository,
        access: AttemptAccessService,
        timing: TimingService,
        clock: Clock,
    ) -> None:
        self._session = session
        self._attempts = attempts
        self._attempt_questions = attempt_questions
        self._flags = flags
        self._access = access
        self._timing = timing
        self._clock = clock

    def set_flag(
        self, attempt_id: str, learner_id: str, question_id: str, flagged: bool
    ) -> dict[str, Any]:
        """Set the flag state for a delivered question.

        Flagging is a learner modification, so it is refused once the attempt is
        locked — a submitted attempt is immutable in every respect, not just answers.
        """
        self._access.load_for_write(attempt_id, learner_id)
        now = self._clock.now()

        # Re-checked inside the write path for the same reason as answer saves: a
        # concurrent expiry submission must win over a late flag change.
        fresh = self._attempts.get_for_learner(attempt_id, learner_id)
        if fresh is None:
            raise errors.attempt_not_found(attempt_id)
        self._access.assert_writable(fresh)

        question = self._attempt_questions.find_by_question_id(attempt_id, question_id)
        if question is None:
            raise errors.invalid_flag_operation(
                "The question is not part of this attempt.",
                attemptId=attempt_id,
                questionId=question_id,
            )

        flag = self._flags.set_flag(
            flag_id=new_id(),
            attempt_id=attempt_id,
            attempt_question_id=question.id,
            question_id=question.question_id,
            flagged=flagged,
            now=now,
        )
        self._attempts.touch_activity(attempt_id, now)
        self._session.commit()

        return {
            "questionId": question.question_id,
            "position": question.position,
            "flagged": bool(flag.flagged),
            "flaggedAt": iso_or_none(flag.flagged_at),
            "updatedAt": to_iso(flag.updated_at),
        }

    def list_flags(self, attempt_id: str, learner_id: str) -> dict[str, Any]:
        """Flag state for every delivered question, including unflagged ones."""
        attempt = self._access.load(attempt_id, learner_id).attempt
        questions = self._attempt_questions.list_for_attempt(attempt_id)
        by_question = {
            flag.attempt_question_id: flag for flag in self._flags.list_for_attempt(attempt_id)
        }

        flags = []
        for question in questions:
            flag = by_question.get(question.id)
            flags.append(
                {
                    "questionId": question.question_id,
                    "position": question.position,
                    "flagged": bool(flag.flagged) if flag else False,
                    "flaggedAt": iso_or_none(flag.flagged_at) if flag else None,
                    "updatedAt": to_iso(flag.updated_at) if flag else to_iso(question.created_at),
                }
            )

        return {
            "attemptId": attempt.id,
            "status": attempt.status,
            "flaggedCount": sum(1 for item in flags if item["flagged"]),
            "flags": flags,
            "timing": self._timing.compute(attempt).to_dict(),
        }
