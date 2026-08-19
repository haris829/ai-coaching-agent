"""Answer persistence and autosave.

The save API is the backend half of the 30-second autosave requirement. The backend
runs no timer of its own: the client saves on whatever cadence it likes and this
service guarantees the properties that make such a loop safe.

* **Idempotent** — re-saving an unchanged answer succeeds and does not advance the
  revision, so a periodic autosave is free to repeat itself.
* **Last-write-wins, but verifiable** — a client may pass ``expected_revision`` to
  detect that another tab or device moved the answer on.
* **Atomic in batches** — a batch autosave validates every entry first and persists
  all or nothing, so one malformed answer cannot leave the attempt half-saved.
* **Closed against expiry** — the attempt's state is re-verified inside the write
  transaction, so a save racing the timer cannot land after submission.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import Clock, to_iso
from app.modules.attempt_delivery.domain import errors
from app.modules.attempt_delivery.domain.answer_validation import hash_answer, validate_answer
from app.modules.attempt_delivery.domain.enums import AnswerSource
from app.modules.attempt_delivery.ids import new_id
from app.modules.attempt_delivery.integration.uc02.types import BankQuestion
from app.modules.attempt_delivery.models import QuizAttempt
from app.modules.attempt_delivery.repositories.answer_repository import (
    AnswerRepository,
    AnswerWrite,
)
from app.modules.attempt_delivery.repositories.attempt_question_repository import (
    AttemptQuestionRepository,
)
from app.modules.attempt_delivery.repositories.attempt_repository import AttemptRepository
from app.modules.attempt_delivery.services.attempt_access_service import AttemptAccessService
from app.modules.attempt_delivery.services.timing_service import TimingService


@dataclass(frozen=True, slots=True)
class SaveAnswerInput:
    question_id: str
    #: Raw payload; ``None`` clears the answer.
    response: Any
    source: AnswerSource = AnswerSource.MANUAL
    #: Optional optimistic-concurrency guard.
    expected_revision: int | None = None


@dataclass(frozen=True, slots=True)
class SavedAnswerView:
    question_id: str
    position: int
    answered: bool
    complete: bool
    response: Any
    revision: int
    source: str
    saved_at: str
    #: False when the payload matched what was already stored.
    changed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "questionId": self.question_id,
            "position": self.position,
            "answered": self.answered,
            "complete": self.complete,
            "response": self.response,
            "revision": self.revision,
            "source": self.source,
            "savedAt": self.saved_at,
            "changed": self.changed,
        }


@dataclass(frozen=True, slots=True)
class BatchSaveResult:
    saved: list[SavedAnswerView] = field(default_factory=list)
    timing: dict[str, Any] = field(default_factory=dict)
    persisted_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "saved": [view.to_dict() for view in self.saved],
            "savedCount": len(self.saved),
            "changedCount": sum(1 for view in self.saved if view.changed),
            "timing": self.timing,
            "persistedAt": self.persisted_at,
        }


class AnswerService:
    """Validates and persists learner answers."""

    __slots__ = (
        "_session",
        "_attempts",
        "_attempt_questions",
        "_answers",
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
        answers: AnswerRepository,
        access: AttemptAccessService,
        timing: TimingService,
        clock: Clock,
    ) -> None:
        self._session = session
        self._attempts = attempts
        self._attempt_questions = attempt_questions
        self._answers = answers
        self._access = access
        self._timing = timing
        self._clock = clock

    def save(self, attempt_id: str, learner_id: str, entry: SaveAnswerInput) -> dict[str, Any]:
        """Save one answer."""
        result = self.save_many(attempt_id, learner_id, [entry])
        if not result.saved:  # pragma: no cover - defensive
            raise errors.internal_error()
        return {
            "answer": result.saved[0].to_dict(),
            "timing": result.timing,
            "persistedAt": result.persisted_at,
        }

    def save_many(
        self, attempt_id: str, learner_id: str, entries: Sequence[SaveAnswerInput]
    ) -> BatchSaveResult:
        """Save a batch of answers atomically.

        This is the shape a 30-second autosave uses: one round trip that flushes
        everything the learner has touched. All entries are validated before anything
        is written, so an invalid entry rejects the whole batch and leaves stored state
        exactly as it was — the client can then surface a save-failed warning and retry.
        """
        if not entries:
            raise errors.validation_error("At least one answer must be supplied.")

        seen: set[str] = set()
        duplicates: set[str] = set()
        for entry in entries:
            if entry.question_id in seen:
                duplicates.add(entry.question_id)
            seen.add(entry.question_id)
        if duplicates:
            raise errors.validation_error(
                "The same question must not appear twice in one save request.",
                duplicateQuestionIds=sorted(duplicates),
            )

        # Settles any elapsed time limit and rejects locked attempts before work starts.
        self._access.load_for_write(attempt_id, learner_id)

        persisted_at = self._clock.now()

        # Re-read and re-check inside the write transaction. This is what closes the
        # "answer save races timer expiry" window: the write takes an immediate lock,
        # and if an expiry submission committed first the attempt is no longer writable,
        # so this save is rejected rather than landing on a submitted attempt.
        fresh = self._attempts.get_for_learner(attempt_id, learner_id)
        if fresh is None:
            raise errors.attempt_not_found(attempt_id)
        self._access.assert_writable(fresh)

        # Pass 1 — resolve and validate everything. Nothing is written yet.
        prepared = []
        for entry in entries:
            question = self._attempt_questions.find_by_question_id(attempt_id, entry.question_id)
            if question is None:
                raise errors.question_unavailable(entry.question_id)

            snapshot: BankQuestion = BankQuestion.from_dict(question.question_snapshot)
            validated = validate_answer(snapshot, entry.response)

            if entry.expected_revision is not None:
                existing = self._answers.find(attempt_id, question.id)
                current_revision = existing.revision if existing else 0
                if current_revision != entry.expected_revision:
                    raise errors.answer_revision_conflict(
                        questionId=entry.question_id,
                        expectedRevision=entry.expected_revision,
                        currentRevision=current_revision,
                    )

            prepared.append((entry, question, validated))

        # Pass 2 — persist. Any raise above happened before a single write.
        views: list[SavedAnswerView] = []
        for entry, question, validated in prepared:
            result = self._answers.upsert(
                AnswerWrite(
                    id=new_id(),
                    attempt_id=attempt_id,
                    attempt_question_id=question.id,
                    question_id=question.question_id,
                    answered=validated.answered,
                    complete=validated.complete,
                    canonical=validated.canonical,
                    response_hash=hash_answer(validated.canonical),
                    source=entry.source,
                    saved_at=persisted_at,
                )
            )
            views.append(
                SavedAnswerView(
                    question_id=question.question_id,
                    position=question.position,
                    answered=bool(result.answer.answered),
                    complete=bool(result.answer.complete),
                    response=result.answer.response,
                    revision=result.answer.revision,
                    source=result.answer.source,
                    saved_at=to_iso(result.answer.saved_at),
                    changed=result.changed,
                )
            )

        self._attempts.touch_activity(attempt_id, persisted_at)
        self._session.commit()

        updated = self._attempts.get_for_learner(attempt_id, learner_id)
        if updated is None:  # pragma: no cover - defensive
            raise errors.attempt_not_found(attempt_id)

        return BatchSaveResult(
            saved=views,
            timing=self._timing.compute(updated).to_dict(),
            persisted_at=to_iso(persisted_at),
        )

    def list_answers(self, attempt_id: str, learner_id: str) -> dict[str, Any]:
        """The latest persisted answers for an attempt.

        This is the reload path: after a refresh or reconnection the client discards its
        own state and rebuilds from here. Every delivered question is listed — answered
        or not — rather than only those with stored responses.
        """
        attempt: QuizAttempt = self._access.load(attempt_id, learner_id).attempt
        questions = self._attempt_questions.list_for_attempt(attempt_id)
        by_question = {
            answer.attempt_question_id: answer
            for answer in self._answers.list_for_attempt(attempt_id)
        }

        answers = []
        for question in questions:
            answer = by_question.get(question.id)
            answers.append(
                {
                    "questionId": question.question_id,
                    "position": question.position,
                    "questionType": question.question_type,
                    "answered": bool(answer.answered) if answer else False,
                    "complete": bool(answer.complete) if answer else False,
                    "response": answer.response if answer else None,
                    "revision": answer.revision if answer else 0,
                    "source": answer.source if answer else None,
                    "savedAt": to_iso(answer.saved_at) if answer else None,
                }
            )

        return {
            "attemptId": attempt.id,
            "status": attempt.status,
            "totalQuestions": attempt.total_questions,
            "answeredCount": sum(1 for item in answers if item["answered"]),
            "completeCount": sum(1 for item in answers if item["complete"]),
            "answers": answers,
            "timing": self._timing.compute(attempt).to_dict(),
        }

    def list_revisions(self, attempt_id: str, learner_id: str) -> list[dict[str, Any]]:
        """Audit trail of accepted saves.

        Useful for support, and for proving an autosave actually landed.
        """
        self._access.load(attempt_id, learner_id)
        questions = self._attempt_questions.list_for_attempt(attempt_id)
        question_ids = {question.id: question.question_id for question in questions}
        return [
            {
                "questionId": question_ids.get(revision.attempt_question_id),
                "revision": revision.revision,
                "answered": bool(revision.answered),
                "complete": bool(revision.complete),
                "response": revision.response,
                "source": revision.source,
                "savedAt": to_iso(revision.saved_at),
            }
            for revision in self._answers.list_revisions(attempt_id)
        ]
