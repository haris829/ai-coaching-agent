"""Scoring, end to end.

The use case is small but its failure behaviour is the point, so the shape mirrors UC-03's
submission service: decide, persist, and make every step safe to repeat.

Idempotency
-----------
``score`` is safe to call any number of times, from anywhere -- the submission pipeline, a retry
endpoint, an operator script:

* the first call claims the one result row for the attempt (``uq_qr_attempt_results_attempt_id``);
* a call against an already-``SCORED`` attempt **replays** the stored result rather than
  recomputing it, and never writes;
* a call against a ``PENDING_SCORE`` result re-runs and confirms it if the data now allows;
* two concurrent calls produce one result: the loser of the insert adopts the winner's row, and
  the loser of the confirming compare-and-set replays it.

Failure
-------
Nothing here can undo a submission. If the attempt cannot be scored -- a missing answer key, a
zero maximum, an unreadable answer -- the result is recorded ``PENDING_SCORE`` with the reason,
which is what a learner sees as "Submitted -- Pending Score", and the run can be retried once the
data is fixed. A persistence failure rolls back and reports a retryable 503; it does not leave a
half-written result, because the question scores and the confirmation are one transaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.time import Clock
from app.modules.scoring.domain import errors
from app.modules.scoring.domain.answer_key import AnswerKey, derive_answer_key
from app.modules.scoring.domain.enums import SCORING_ALGORITHM_VERSION, ResultStatus
from app.modules.scoring.domain.scoring import QuestionScore, aggregate, score_question
from app.modules.scoring.integration.attempt_delivery.port import AttemptSourcePort
from app.modules.scoring.integration.attempt_delivery.types import (
    DeliveredQuestion,
    SubmittedAttempt,
)
from app.modules.scoring.integration.marking_policy import translate
from app.modules.scoring.integration.question_bank.port import (
    AnswerKeyPort,
    QuestionVersionRef,
)
from app.modules.scoring.models import AttemptResult, QuestionScoreRow
from app.modules.scoring.repositories import ResultRepository

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ScoringOutcome:
    """What one call to :meth:`ScoringService.score` did."""

    result: AttemptResult
    question_scores: list[QuestionScoreRow]
    #: True when the result row was created by this call.
    created: bool
    #: True when this call returned an existing confirmed score without recomputing it.
    replayed: bool

    @property
    def confirmed(self) -> bool:
        return self.result.status == str(ResultStatus.SCORED)


class ScoringService:
    """Score a submitted attempt and persist the result."""

    __slots__ = ("_session", "_results", "_attempts", "_answer_keys", "_clock")

    def __init__(
        self,
        *,
        session: Session,
        results: ResultRepository,
        attempts: AttemptSourcePort,
        answer_keys: AnswerKeyPort,
        clock: Clock,
    ) -> None:
        self._session = session
        self._results = results
        self._attempts = attempts
        self._answer_keys = answer_keys
        self._clock = clock

    # ------------------------------------------------------------------ read

    def find_result(self, attempt_id: str, *, learner_id: str | None = None) -> AttemptResult:
        """The stored result, scoped to its owner. Raises rather than returning ``None``."""
        # Ownership is resolved through the attempt, which is where it is recorded.
        if (
            learner_id is not None
            and self._attempts.get_attempt(attempt_id, learner_id=learner_id) is None
        ):
            raise errors.attempt_not_found(attempt_id)
        result = self._results.get_by_attempt(attempt_id)
        if result is None:
            raise errors.result_not_found(attempt_id)
        return result

    def question_scores(self, result_id: str) -> list[QuestionScoreRow]:
        return self._results.list_question_scores(result_id)

    def list_results(self, learner_id: str, *, quiz_id: str | None = None) -> list[AttemptResult]:
        return self._results.list_for_learner(learner_id, quiz_id=quiz_id)

    # ----------------------------------------------------------------- score

    def score(self, attempt_id: str, *, learner_id: str | None = None) -> ScoringOutcome:
        """Score the attempt, or replay the score it already has. ``learner_id`` scopes the lookup
        when the call comes from a learner request; the submission pipeline passes none, because
        it is acting on an attempt it already resolved."""
        attempt = self._attempts.get_attempt(attempt_id, learner_id=learner_id)
        if attempt is None:
            raise errors.attempt_not_found(attempt_id)
        # ``locked`` covers both of UC-03's committed states. A submission still completing
        # downstream has already frozen its answers, so scoring it is correct -- that is how the
        # pipeline scores an attempt on the way through submission.
        if not attempt.locked:
            raise errors.attempt_not_submitted(attempt_id, attempt.status)

        existing = self._results.get_by_attempt(attempt_id)
        if existing is not None and existing.status == str(ResultStatus.SCORED):
            # A confirmed score is immutable, so this is a replay and not a no-op update.
            return ScoringOutcome(
                result=existing,
                question_scores=self._results.list_question_scores(existing.id),
                created=False,
                replayed=True,
            )

        result, created = self._claim(attempt, existing)
        now = self._clock.now()
        self._results.record_run(result.id, now)

        scores = self._compute(attempt)
        totals = aggregate(scores)
        delivered_by_id = {question.question_id: question for question in attempt.questions}

        counts: dict[str, Any] = {
            "total_marks": totals.total_marks,
            "maximum_marks": totals.maximum_marks,
            "percentage": totals.percentage,
            "total_questions": totals.total_questions,
            "correct_count": totals.correct_count,
            "incorrect_count": totals.incorrect_count,
            "unanswered_count": totals.unanswered_count,
            "time_taken_seconds": attempt.time_taken_seconds,
            "submitted_at": attempt.submitted_at,
            "algorithm_version": SCORING_ALGORITHM_VERSION,
        }

        try:
            if totals.confirmable:
                self._results.replace_question_scores(
                    result.id,
                    [
                        _row_fields(score, delivered_by_id.get(score.question_id))
                        for score in scores
                    ],
                    now=now,
                )
                confirmed = self._results.mark_scored(result.id, now=now, **counts)
                if not confirmed:
                    # Another run confirmed first. Its stored score is authoritative; ours is
                    # discarded rather than layered on top.
                    self._session.rollback()
                    winner = self._results.get_by_attempt(attempt_id)
                    if winner is None:
                        # pragma: no cover - defensive
                        raise errors.internal_error()
                    return ScoringOutcome(
                        result=winner,
                        question_scores=self._results.list_question_scores(winner.id),
                        created=False,
                        replayed=True,
                    )
            else:
                # Marks are still recorded so an operator can see what the run computed, but the
                # result stays PENDING_SCORE: nobody is shown a percentage derived from broken data.
                self._results.mark_pending_failure(
                    result.id,
                    failure_code=str(totals.anomalies[0]["code"]),
                    failure_message=_anomaly_message(totals.anomalies),
                    anomalies=list(totals.anomalies),
                    now=now,
                    **counts,
                )
                logger.warning(
                    "scoring.pending",
                    extra={
                        "attemptId": attempt_id,
                        "resultId": result.id,
                        "anomalies": [item["code"] for item in totals.anomalies],
                    },
                )
            self._session.commit()
        except SQLAlchemyError as exc:
            self._session.rollback()
            logger.error(
                "scoring.persistence_failed", extra={"attemptId": attempt_id}, exc_info=exc
            )
            raise errors.persistence_failed("score_attempt") from exc

        stored = self._results.get_by_attempt(attempt_id)
        if stored is None:
            # pragma: no cover - defensive
            raise errors.internal_error()
        return ScoringOutcome(
            result=stored,
            question_scores=self._results.list_question_scores(stored.id),
            created=created,
            replayed=False,
        )

    # -------------------------------------------------------------- internals

    def _claim(
        self, attempt: SubmittedAttempt, existing: AttemptResult | None
    ) -> tuple[AttemptResult, bool]:
        """Get or create the attempt's single result row."""
        if existing is not None:
            return existing, False

        now = self._clock.now()
        try:
            with self._session.begin_nested():
                created = self._results.insert_pending(
                    attempt_id=attempt.attempt_id,
                    submission_id=attempt.submission_id,
                    learner_id=attempt.learner_id,
                    course_id=attempt.course_id,
                    quiz_id=attempt.quiz_id,
                    attempt_number=attempt.attempt_number,
                    configuration_version_id=attempt.configuration_version_id,
                    configuration_version_number=attempt.configuration_version_number,
                    pass_mark_percentage=attempt.pass_mark_percentage,
                    started_at=attempt.started_at,
                    submitted_at=attempt.submitted_at,
                    created_at=now,
                    updated_at=now,
                )
            return created, True
        except IntegrityError:
            # A concurrent run claimed it first; adopt its row rather than creating a second.
            winner = self._results.get_by_attempt(attempt.attempt_id)
            if winner is None:  # pragma: no cover - the unique index is the only cause
                raise
            return winner, False

    def _compute(self, attempt: SubmittedAttempt) -> list[QuestionScore]:
        """Resolve every answer key, then mark every delivered question."""
        refs = [
            QuestionVersionRef(question.question_id, question.question_version)
            for question in attempt.questions
        ]
        keys = self._answer_keys.find_answer_keys(refs) if refs else {}

        return [
            score_question(question, self._key_for(question, keys))
            for question in attempt.questions
        ]

    def _key_for(
        self,
        question: DeliveredQuestion,
        keys: dict[QuestionVersionRef, AnswerKey],
    ) -> AnswerKey | None:
        """The bank's snapshot key, or the copy frozen onto the attempt. Falling back matters: a
        question bank that has lost a snapshot row must not turn a learner's correct answers into
        zeros, because UC-03 froze the same answer key onto the attempt at delivery. Which copy
        was used is recorded per question."""
        key = keys.get(QuestionVersionRef(question.question_id, question.question_version))
        if key is not None and key.is_usable():
            return key

        derived = derive_answer_key(
            question,
            # The authored strategy travels in UC-03's frozen snapshot; translating it keeps a
            # partial-credit question marked the way it was configured.
            marking_policy=translate(question.extra.get("scoringStrategy")),
            explanation=key.explanation if key is not None else None,
            topics=key.topics if key is not None else (),
        )
        return derived if derived.is_usable() else None


def _anomaly_message(anomalies: tuple[dict[str, Any], ...]) -> str:
    codes = sorted({str(item["code"]) for item in anomalies})
    return (
        "The attempt could not be scored: "
        + ", ".join(codes)
        + ". The submission is unaffected and scoring can be retried."
    )


def _row_fields(score: QuestionScore, delivered: DeliveredQuestion | None) -> dict[str, Any]:
    """Flatten one domain score into the columns ``qr_question_scores`` stores. The question text
    and the learner's raw answer are copied in from the delivered snapshot, so UC-06 can build a
    feedback report from this row alone. That is what keeps a historical report identical after
    the question bank is edited: the report never re-reads the question."""
    return {
        "attempt_question_id": score.attempt_question_id,
        "question_id": score.question_id,
        "question_version": score.question_version,
        "question_type": str(score.question_type),
        "position": score.position,
        "awarded_marks": score.awarded_marks,
        "maximum_marks": score.maximum_marks,
        "raw_marks": score.raw_marks,
        "deduction": score.deduction,
        "outcome": str(score.outcome),
        "answered": score.answered,
        "question_text": delivered.prompt if delivered is not None else "",
        "scenario_text": delivered.scenario_text if delivered is not None else None,
        "explanation": score.explanation,
        "topics": list(score.topics) or None,
        "learner_answer": delivered.response if delivered is not None else None,
        "learner_answer_display": score.learner_answer_display,
        "correct_answer_display": score.correct_answer_display,
        "option_marks": [mark.to_dict() for mark in score.option_marks] or None,
        "anomaly": str(score.anomaly) if score.anomaly is not None else None,
        "answer_key_source": str(score.key_source) if score.key_source is not None else None,
    }
