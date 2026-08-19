"""Submission.

The design separates two things that fail independently:

1. **Local commit** — lock the attempt, freeze its answers, record the submission
   row. Transactional and durable *before* anything else happens.
2. **Downstream hand-off** — notify the grading capability through
   :class:`SubmissionDispatchPort`. This is a network boundary and may fail
   transiently, which is what the PENDING state exists for.

Idempotency is enforced by the database, not by a read-then-write check:
``ux_submission_idempotency`` collapses retries of the same logical request, and
``ux_submission_single_success`` makes more than one successful submission per attempt
impossible even under a race.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import Clock, iso_or_none, to_iso
from app.modules.attempt_delivery.domain import errors
from app.modules.attempt_delivery.domain.enums import (
    AttemptStatus,
    SubmissionReason,
    SubmissionState,
)
from app.modules.attempt_delivery.ids import new_id
from app.modules.attempt_delivery.integration.submission_dispatch.port import (
    DispatchAnswer,
    SubmissionDispatchPort,
    SubmissionDispatchRequest,
    TransientDispatchError,
)
from app.modules.attempt_delivery.models import AttemptSubmission, QuizAttempt
from app.modules.attempt_delivery.repositories.answer_repository import AnswerRepository
from app.modules.attempt_delivery.repositories.attempt_question_repository import (
    AttemptQuestionRepository,
)
from app.modules.attempt_delivery.repositories.attempt_repository import AttemptRepository
from app.modules.attempt_delivery.repositories.flag_repository import FlagRepository
from app.modules.attempt_delivery.repositories.submission_repository import SubmissionRepository
from app.modules.attempt_delivery.services.timing_service import TimingService


@dataclass(frozen=True, slots=True)
class QuestionOutline:
    """Per-question navigation state."""

    question_id: str
    position: int
    question_type: str
    answered: bool
    complete: bool
    flagged: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "questionId": self.question_id,
            "position": self.position,
            "questionType": self.question_type,
            "answered": self.answered,
            "complete": self.complete,
            "flagged": self.flagged,
        }


@dataclass(frozen=True, slots=True)
class AttemptSummary:
    total_questions: int
    answered_count: int
    complete_count: int
    unanswered_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "totalQuestions": self.total_questions,
            "answeredCount": self.answered_count,
            "completeCount": self.complete_count,
            "unansweredCount": self.unanswered_count,
        }


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    body: dict[str, Any]
    #: True when this response replays an already-completed submission.
    idempotent_replay: bool


@dataclass(frozen=True, slots=True)
class _Proceed:
    submission_id: str
    dispatch: SubmissionDispatchRequest


@dataclass(frozen=True, slots=True)
class _Replay:
    body: dict[str, Any]


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class SubmissionService:
    """Preview, confirm, retry and time-expiry submission."""

    __slots__ = (
        "_session",
        "_attempts",
        "_attempt_questions",
        "_answers",
        "_flags",
        "_submissions",
        "_dispatcher",
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
        flags: FlagRepository,
        submissions: SubmissionRepository,
        dispatcher: SubmissionDispatchPort,
        timing: TimingService,
        clock: Clock,
    ) -> None:
        self._session = session
        self._attempts = attempts
        self._attempt_questions = attempt_questions
        self._answers = answers
        self._flags = flags
        self._submissions = submissions
        self._dispatcher = dispatcher
        self._timing = timing
        self._clock = clock

    # ---------------------------------------------------------------- outline

    def outline(self, attempt_id: str) -> list[QuestionOutline]:
        """Per-question answered/flagged outline.

        Shared by the navigation-state endpoint and the submission preview so both
        always agree.
        """
        questions = self._attempt_questions.list_for_attempt(attempt_id)
        answers = {
            answer.attempt_question_id: answer
            for answer in self._answers.list_for_attempt(attempt_id)
        }
        flags = {
            flag.attempt_question_id: flag for flag in self._flags.list_for_attempt(attempt_id)
        }

        outline: list[QuestionOutline] = []
        for question in questions:
            answer = answers.get(question.id)
            flag = flags.get(question.id)
            outline.append(
                QuestionOutline(
                    question_id=question.question_id,
                    position=question.position,
                    question_type=question.question_type,
                    answered=bool(answer.answered) if answer else False,
                    complete=bool(answer.complete) if answer else False,
                    flagged=bool(flag.flagged) if flag else False,
                )
            )
        return outline

    def summarise(self, attempt_id: str) -> AttemptSummary:
        outline = self.outline(attempt_id)
        answered = sum(1 for entry in outline if entry.answered)
        complete = sum(1 for entry in outline if entry.complete)
        return AttemptSummary(
            total_questions=len(outline),
            answered_count=answered,
            complete_count=complete,
            unanswered_count=len(outline) - complete,
        )

    # ---------------------------------------------------------------- preview

    def preview(self, attempt: QuizAttempt) -> dict[str, Any]:
        """Build the submission summary the confirmation step is based on.

        Performs no writes and never submits, however many times it is called.
        Committing requires the separate confirmed POST.
        """
        outline = self.outline(attempt.id)
        summary = self.summarise(attempt.id)

        unanswered = [
            {"position": entry.position, "questionId": entry.question_id}
            for entry in outline
            if not entry.complete
        ]
        flagged = [
            {"position": entry.position, "questionId": entry.question_id}
            for entry in outline
            if entry.flagged
        ]

        blockers: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []

        if attempt.status == str(AttemptStatus.SUBMITTED):
            blockers.append(
                {
                    "code": "ATTEMPT_ALREADY_SUBMITTED",
                    "message": "The attempt has already been submitted.",
                }
            )
        elif attempt.status == str(AttemptStatus.SUBMISSION_PENDING):
            blockers.append(
                {
                    "code": "ATTEMPT_SUBMISSION_PENDING",
                    "message": (
                        "A submission is pending for this attempt. "
                        "Retry it instead of submitting again."
                    ),
                }
            )

        allow_incomplete = bool(
            attempt.configuration_snapshot.get("allowIncompleteSubmission", True)
        )
        if unanswered:
            if allow_incomplete:
                warnings.append(
                    {
                        "code": "UNANSWERED_QUESTIONS",
                        "message": (
                            f"{len(unanswered)} question(s) are unanswered "
                            "and will be submitted as such."
                        ),
                    }
                )
            else:
                blockers.append(
                    {
                        "code": "INCOMPLETE_SUBMISSION_NOT_ALLOWED",
                        "message": (
                            "All questions must be answered before submitting. "
                            f"{len(unanswered)} remain."
                        ),
                    }
                )

        if flagged:
            warnings.append(
                {
                    "code": "FLAGGED_QUESTIONS",
                    "message": f"{len(flagged)} question(s) are still flagged for review.",
                }
            )

        timing = self._timing.compute(attempt)
        if timing.timed and timing.remaining_seconds is not None and timing.remaining_seconds <= 60:
            warnings.append(
                {
                    "code": "TIME_ALMOST_ELAPSED",
                    "message": f"Only {timing.remaining_seconds} second(s) remain.",
                }
            )

        return {
            "attemptId": attempt.id,
            "attemptStatus": attempt.status,
            **summary.to_dict(),
            "unanswered": unanswered,
            "flagged": flagged,
            "allowIncompleteSubmission": allow_incomplete,
            "canSubmit": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "timing": timing.to_dict(),
            # Preview never submits; the client must POST with confirmed: true.
            "requiresConfirmation": True,
            "suggestedIdempotencyKey": f"attempt-{attempt.id}-submit",
        }

    # ------------------------------------------------------------ public API

    def confirm(
        self, attempt: QuizAttempt, *, idempotency_key: str, confirmed: bool
    ) -> SubmissionResult:
        """Confirm and perform a submission.

        ``confirmed`` must be True: the preview endpoint deliberately cannot submit,
        so the commit always carries the learner's explicit confirmation.
        """
        if confirmed is not True:
            raise errors.submission_not_confirmed()
        return self._execute(
            attempt.id,
            idempotency_key=idempotency_key,
            reason=SubmissionReason.LEARNER_CONFIRMED,
            enforce_completeness=True,
        )

    def retry(
        self, attempt: QuizAttempt, *, idempotency_key: str | None = None
    ) -> SubmissionResult:
        """Retry a submission stuck PENDING after a transient downstream failure.

        Reuses the existing submission row, so no duplicate is created.
        """
        if idempotency_key is not None:
            existing = self._submissions.find_by_idempotency_key(attempt.id, idempotency_key)
        else:
            existing = self._submissions.find_pending(
                attempt.id
            ) or self._submissions.find_submitted(attempt.id)

        if existing is None:
            raise errors.no_pending_submission(attempt.id)

        # A completed submission replays its stored response rather than re-running.
        if existing.state == str(SubmissionState.SUBMITTED):
            return SubmissionResult(
                body=dict(existing.response_snapshot or {}), idempotent_replay=True
            )

        return self._execute(
            attempt.id,
            idempotency_key=existing.idempotency_key,
            reason=SubmissionReason(existing.submission_reason),
            # The learner already confirmed; completeness was checked then and must
            # not block a retry.
            enforce_completeness=False,
        )

    def submit_on_expiry(self, attempt: QuizAttempt) -> SubmissionResult:
        """Submit an attempt because its server-authoritative time limit elapsed.

        Uses the latest successfully saved answers as-is, a deterministic idempotency
        key (so repeated sweeps collapse into one submission), and records the
        *deadline* as the submission instant rather than the moment expiry was noticed.
        """
        return self._execute(
            attempt.id,
            idempotency_key=f"time-expiry:{attempt.id}",
            reason=SubmissionReason.TIME_EXPIRED,
            enforce_completeness=False,
            submitted_at_override=attempt.expires_at,
        )

    def describe(self, attempt_id: str) -> dict[str, Any]:
        """Current submission state for the status endpoint."""
        submitted = self._submissions.find_submitted(attempt_id)
        pending = self._submissions.find_pending(attempt_id)
        history = self._submissions.list_for_attempt(attempt_id)
        return {
            "submission": _submission_to_dict(submitted),
            "pendingSubmission": _submission_to_dict(pending),
            "history": [_submission_to_dict(item) for item in history],
        }

    # ----------------------------------------------------------- core engine

    def _execute(
        self,
        attempt_id: str,
        *,
        idempotency_key: str,
        reason: SubmissionReason,
        enforce_completeness: bool,
        submitted_at_override: datetime | None = None,
    ) -> SubmissionResult:
        now = self._clock.now()

        # ---- Phase 1: durable local commit. --------------------------------
        outcome = self._commit_locally(
            attempt_id,
            now=now,
            idempotency_key=idempotency_key,
            reason=reason,
            enforce_completeness=enforce_completeness,
            submitted_at_override=submitted_at_override,
        )
        if isinstance(outcome, _Replay):
            self._session.commit()
            return SubmissionResult(body=outcome.body, idempotent_replay=True)

        # The commit must be durable before the hand-off, so a crash during dispatch
        # leaves a retriable PENDING submission rather than a lost attempt.
        self._session.commit()

        # ---- Phase 2: downstream hand-off, outside any transaction. --------
        try:
            dispatch_result = self._dispatcher.dispatch(outcome.dispatch)
            downstream_reference = dispatch_result.downstream_reference
        except Exception as exc:  # noqa: BLE001 - classified below
            self._handle_dispatch_failure(outcome.submission_id, attempt_id, exc)
            raise  # pragma: no cover - _handle_dispatch_failure always raises

        # ---- Phase 3: finalise. --------------------------------------------
        body = self._finalise(attempt_id, outcome.submission_id, downstream_reference)
        self._session.commit()
        return SubmissionResult(body=body, idempotent_replay=False)

    def _commit_locally(
        self,
        attempt_id: str,
        *,
        now: datetime,
        idempotency_key: str,
        reason: SubmissionReason,
        enforce_completeness: bool,
        submitted_at_override: datetime | None,
    ) -> _Proceed | _Replay:
        """Resolve idempotency, verify submittability, freeze answers, lock the attempt."""
        attempt = self._attempts.get(attempt_id)
        if attempt is None:
            raise errors.attempt_not_found(attempt_id)

        expected_fingerprint = _fingerprint({"attemptId": attempt_id, "reason": str(reason)})
        existing = self._submissions.find_by_idempotency_key(attempt_id, idempotency_key)

        if existing is not None:
            if existing.request_fingerprint != expected_fingerprint:
                raise errors.idempotency_key_reused(idempotency_key)
            if existing.state == str(SubmissionState.SUBMITTED):
                return _Replay(body=dict(existing.response_snapshot or {}))
            # PENDING or FAILED: re-drive the same submission record.
            self._submissions.record_retry(existing.id, now)
            submission_id = existing.id
        else:
            # A different key can never submit an already-submitted attempt.
            already = self._submissions.find_submitted(attempt_id)
            if already is not None:
                raise errors.duplicate_submission(
                    attempt_id,
                    existingSubmissionId=already.id,
                    existingIdempotencyKey=already.idempotency_key,
                    submittedAt=iso_or_none(attempt.submitted_at),
                )
            if attempt.status == str(AttemptStatus.SUBMISSION_PENDING):
                raise errors.attempt_submission_pending(attempt_id)
            if attempt.status == str(AttemptStatus.SUBMITTED):
                raise errors.attempt_already_submitted(
                    attempt_id, iso_or_none(attempt.submitted_at)
                )
            submission_id = None  # claimed below

        if enforce_completeness:
            self._assert_submittable(attempt)

        summary = self.summarise(attempt_id)

        if submission_id is None:
            submission_id = new_id()
            try:
                # A savepoint keeps a losing race from poisoning the outer transaction.
                with self._session.begin_nested():
                    self._submissions.insert_pending(
                        submission_id=submission_id,
                        attempt_id=attempt_id,
                        idempotency_key=idempotency_key,
                        request_fingerprint=expected_fingerprint,
                        submission_reason=reason,
                        answered_count=summary.answered_count,
                        total_questions=summary.total_questions,
                        now=now,
                    )
            except IntegrityError:
                # A concurrent request claimed the key first — adopt its record.
                winner = self._submissions.find_by_idempotency_key(attempt_id, idempotency_key)
                if winner is None:
                    raise
                if winner.state == str(SubmissionState.SUBMITTED):
                    return _Replay(body=dict(winner.response_snapshot or {}))
                submission_id = winner.id

        # Lock the attempt. The compare-and-set on `status = ACTIVE` is what resolves a
        # race with a concurrent expiry sweep: the loser simply observes the attempt is
        # already locked rather than double-locking or corrupting the lifecycle.
        if attempt.status == str(AttemptStatus.ACTIVE):
            self._attempts.lock_for_submission(
                attempt_id,
                status=AttemptStatus.SUBMISSION_PENDING,
                reason=reason,
                submitted_at=submitted_at_override or now,
                now=now,
            )

        locked = self._attempts.get(attempt_id)
        if locked is None:  # pragma: no cover - defensive
            raise errors.attempt_not_found(attempt_id)

        return _Proceed(
            submission_id=submission_id,
            dispatch=self._build_dispatch_request(locked, submission_id, summary),
        )

    def _finalise(
        self, attempt_id: str, submission_id: str, downstream_reference: str | None
    ) -> dict[str, Any]:
        finalised_at = self._clock.now()
        summary = self.summarise(attempt_id)

        submission = self._submissions.get(submission_id)
        if submission is None:  # pragma: no cover - defensive
            raise errors.internal_error()

        # Another in-flight call may have finalised this submission first; its stored
        # response is authoritative.
        if submission.state == str(SubmissionState.SUBMITTED):
            return dict(submission.response_snapshot or {})

        self._attempts.mark_submitted(attempt_id, finalised_at)
        attempt = self._attempts.get(attempt_id)
        if attempt is None:  # pragma: no cover - defensive
            raise errors.attempt_not_found(attempt_id)

        body: dict[str, Any] = {
            "submission": {
                "id": submission.id,
                "state": str(SubmissionState.SUBMITTED),
                "reason": submission.submission_reason,
                "attemptCount": submission.attempt_count,
                "answeredCount": summary.answered_count,
                "totalQuestions": summary.total_questions,
                "requestedAt": to_iso(submission.requested_at),
                "completedAt": to_iso(finalised_at),
                "downstreamReference": downstream_reference,
            },
            "attempt": {
                "attemptId": attempt.id,
                "status": attempt.status,
                "submittedAt": iso_or_none(attempt.submitted_at),
                "finalisedAt": iso_or_none(attempt.finalised_at),
                "submissionReason": attempt.submission_reason,
            },
            "summary": summary.to_dict(),
        }

        stored = self._submissions.mark_submitted(
            submission_id=submission.id,
            answered_count=summary.answered_count,
            response_snapshot=body,
            downstream_reference=downstream_reference,
            now=finalised_at,
        )
        if not stored:
            # Lost the finalisation race; replay whatever the winner stored.
            winner = self._submissions.find_submitted(attempt_id)
            if winner is not None:
                return dict(winner.response_snapshot or {})
        return body

    def _handle_dispatch_failure(self, submission_id: str, attempt_id: str, exc: Exception) -> None:
        """Classify a hand-off failure and leave the system in a coherent state."""
        self._session.rollback()
        now = self._clock.now()
        message = str(exc) or exc.__class__.__name__

        if isinstance(exc, TransientDispatchError):
            # Keep the attempt locked and the submission retriable. The learner's
            # answers are already frozen, so a retry submits exactly what they left.
            self._submissions.mark_pending_failure(
                submission_id, "DISPATCH_TRANSIENT_FAILURE", message, now
            )
            self._session.commit()
            raise errors.submission_failed(
                "The attempt was recorded but the submission could not be completed. "
                "It is pending and can be retried.",
                retryable=True,
                attemptId=attempt_id,
                submissionId=submission_id,
                submissionState=str(SubmissionState.PENDING),
                attemptStatus=str(AttemptStatus.SUBMISSION_PENDING),
            )

        # Permanent failure: release the attempt so the learner is not stranded with
        # one they can neither edit nor submit.
        self._submissions.mark_failed(submission_id, "DISPATCH_PERMANENT_FAILURE", message, now)
        self._attempts.unlock_after_failure(attempt_id, now)
        self._session.commit()
        raise errors.submission_failed(
            "The submission failed permanently and was not recorded.",
            retryable=False,
            attemptId=attempt_id,
            submissionId=submission_id,
            submissionState=str(SubmissionState.FAILED),
            attemptStatus=str(AttemptStatus.ACTIVE),
        )

    def _assert_submittable(self, attempt: QuizAttempt) -> None:
        if bool(attempt.configuration_snapshot.get("allowIncompleteSubmission", True)):
            return
        outline = self.outline(attempt.id)
        unanswered = [entry for entry in outline if not entry.complete]
        if unanswered:
            raise errors.attempt_not_submittable(
                attempt.id,
                attempt.status,
                unansweredCount=len(unanswered),
                unanswered=[
                    {"position": entry.position, "questionId": entry.question_id}
                    for entry in unanswered
                ],
                allowIncompleteSubmission=False,
            )

    def _build_dispatch_request(
        self, attempt: QuizAttempt, submission_id: str, summary: AttemptSummary
    ) -> SubmissionDispatchRequest:
        questions = self._attempt_questions.list_for_attempt(attempt.id)
        answers = {
            answer.attempt_question_id: answer
            for answer in self._answers.list_for_attempt(attempt.id)
        }
        return SubmissionDispatchRequest(
            attempt_id=attempt.id,
            submission_id=submission_id,
            learner_id=attempt.learner_id,
            course_id=attempt.course_id,
            quiz_id=attempt.quiz_id,
            configuration_version_id=attempt.configuration_version_id,
            submitted_at=to_iso(attempt.submitted_at or self._clock.now()),
            submission_reason=attempt.submission_reason or str(SubmissionReason.LEARNER_CONFIRMED),
            answered_count=summary.answered_count,
            total_questions=summary.total_questions,
            answers=tuple(
                DispatchAnswer(
                    question_id=question.question_id,
                    position=question.position,
                    answered=bool(answers[question.id].answered)
                    if question.id in answers
                    else False,
                    response=answers[question.id].response if question.id in answers else None,
                )
                for question in questions
            ),
        )


def _submission_to_dict(submission: AttemptSubmission | None) -> dict[str, Any] | None:
    if submission is None:
        return None
    return {
        "id": submission.id,
        "attemptId": submission.attempt_id,
        "idempotencyKey": submission.idempotency_key,
        "state": submission.state,
        "reason": submission.submission_reason,
        "attemptCount": submission.attempt_count,
        "answeredCount": submission.answered_count,
        "totalQuestions": submission.total_questions,
        "downstreamReference": submission.downstream_reference,
        "failureCode": submission.failure_code,
        "failureMessage": submission.failure_message,
        "requestedAt": to_iso(submission.requested_at),
        "lastAttemptedAt": to_iso(submission.last_attempted_at),
        "completedAt": iso_or_none(submission.completed_at),
    }
