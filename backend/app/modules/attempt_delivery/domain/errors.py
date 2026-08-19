"""UC-03's error codes and failure factories.

Every failure that is part of normal operation is raised through one of the factories below.

The exception *class* is the system-wide :class:`app.core.errors.AppError`, subclassed here only to
keep UC-03's positional ``(status, code, message)`` calling convention. That matters for two
reasons: there is one exception hierarchy and one set of HTTP handlers for the whole
application, and
UC-03's structured ``details`` dict lands in the shared envelope's ``context`` slot — the place
reserved for exactly that, alongside the ``details`` *list* used for field-level problems.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.errors import AppError as SharedAppError


class ErrorCode(StrEnum):
    """Stable, machine-readable error codes returned to clients."""

    # ---- request shape ----------------------------------------------------
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHENTICATED = "UNAUTHENTICATED"

    # ---- eligibility ------------------------------------------------------
    LEARNER_NOT_ENROLLED = "LEARNER_NOT_ENROLLED"
    ENROLMENT_NOT_ACTIVE = "ENROLMENT_NOT_ACTIVE"
    MAX_ATTEMPTS_REACHED = "MAX_ATTEMPTS_REACHED"
    ACTIVE_ATTEMPT_EXISTS = "ACTIVE_ATTEMPT_EXISTS"

    # ---- UC-01 configuration boundary -------------------------------------
    QUIZ_NOT_FOUND = "QUIZ_NOT_FOUND"
    QUIZ_NOT_AVAILABLE = "QUIZ_NOT_AVAILABLE"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    CONFIGURATION_VERSION_UNAVAILABLE = "CONFIGURATION_VERSION_UNAVAILABLE"

    # ---- UC-02 question bank boundary -------------------------------------
    QUESTION_BANK_UNAVAILABLE = "QUESTION_BANK_UNAVAILABLE"
    INSUFFICIENT_QUESTIONS = "INSUFFICIENT_QUESTIONS"
    QUESTION_UNAVAILABLE = "QUESTION_UNAVAILABLE"

    # ---- attempt lifecycle ------------------------------------------------
    ATTEMPT_NOT_FOUND = "ATTEMPT_NOT_FOUND"
    NO_ACTIVE_ATTEMPT = "NO_ACTIVE_ATTEMPT"
    ATTEMPT_ALREADY_SUBMITTED = "ATTEMPT_ALREADY_SUBMITTED"
    ATTEMPT_SUBMISSION_PENDING = "ATTEMPT_SUBMISSION_PENDING"
    ATTEMPT_EXPIRED = "ATTEMPT_EXPIRED"
    ATTEMPT_NOT_SUBMITTABLE = "ATTEMPT_NOT_SUBMITTABLE"

    # ---- answers ----------------------------------------------------------
    INVALID_ANSWER = "INVALID_ANSWER"
    ANSWER_REVISION_CONFLICT = "ANSWER_REVISION_CONFLICT"

    # ---- navigation / delivery -------------------------------------------
    QUESTION_PRESENTATION_VIOLATION = "QUESTION_PRESENTATION_VIOLATION"
    INVALID_FLAG_OPERATION = "INVALID_FLAG_OPERATION"

    # ---- submission -------------------------------------------------------
    DUPLICATE_SUBMISSION = "DUPLICATE_SUBMISSION"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    SUBMISSION_NOT_CONFIRMED = "SUBMISSION_NOT_CONFIRMED"
    SUBMISSION_FAILED = "SUBMISSION_FAILED"
    NO_PENDING_SUBMISSION = "NO_PENDING_SUBMISSION"

    # ---- infrastructure ---------------------------------------------------
    DATABASE_ERROR = "DATABASE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(SharedAppError):
    """An expected UC-03 failure, safe to render to the client verbatim.

    A thin adapter over the shared error class: it keeps UC-03's positional signature so the
    factories below read naturally, while the instance *is* a
    :class:`app.core.errors.AppError` — so the application's single exception handler renders it in
    the single envelope, and nothing about UC-03's failures is special-cased.
    """

    def __init__(
        self,
        status: int,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        # UC-03's `details` is structured context about the failure, not a list of field errors,
        # so it maps onto the envelope's `context` slot.
        super().__init__(
            message,
            status_code=status,
            # The enum member, not str(code): ``ErrorCode`` is a StrEnum, so the wire format is
            # identical, but code that catches an AppError can still compare against the taxonomy
            # by identity rather than by string.
            code=code,
            context=details or None,
            retryable=retryable,
        )

    @property
    def status(self) -> int:
        """UC-03 code reads ``.status``; the shared class calls it ``status_code``."""
        return self.status_code

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AppError(status={self.status}, code={self.code}, message={self.message!r})"


# ---------------------------------------------------------------------------
# Factories - one per documented failure mode.
# ---------------------------------------------------------------------------


def validation_error(message: str, **details: Any) -> AppError:
    return AppError(400, ErrorCode.VALIDATION_ERROR, message, details=details or None)


def unauthenticated(message: str = "A learner identity is required.") -> AppError:
    return AppError(401, ErrorCode.UNAUTHENTICATED, message)


def learner_not_enrolled(learner_id: str, course_id: str) -> AppError:
    return AppError(
        403,
        ErrorCode.LEARNER_NOT_ENROLLED,
        "The learner is not enrolled in this course and cannot start an attempt.",
        details={"learnerId": learner_id, "courseId": course_id},
    )


def enrolment_not_active(learner_id: str, course_id: str, status: str) -> AppError:
    return AppError(
        403,
        ErrorCode.ENROLMENT_NOT_ACTIVE,
        "The learner enrolment is not in a state that permits a quiz attempt.",
        details={"learnerId": learner_id, "courseId": course_id, "enrolmentStatus": status},
    )


def max_attempts_reached(used: int, maximum: int) -> AppError:
    return AppError(
        409,
        ErrorCode.MAX_ATTEMPTS_REACHED,
        "The maximum number of attempts has been reached.",
        details={"attemptsUsed": used, "maxAttempts": maximum, "attemptsRemaining": 0},
    )


def active_attempt_exists(attempt_id: str) -> AppError:
    return AppError(
        409,
        ErrorCode.ACTIVE_ATTEMPT_EXISTS,
        "An attempt is already in progress for this quiz. "
        "Resume or submit it before starting another.",
        details={"activeAttemptId": attempt_id},
    )


def quiz_not_found(quiz_id: str) -> AppError:
    return AppError(
        404,
        ErrorCode.QUIZ_NOT_FOUND,
        "The requested quiz does not exist.",
        details={"quizId": quiz_id},
    )


def quiz_not_available(quiz_id: str, reason: str) -> AppError:
    return AppError(
        409,
        ErrorCode.QUIZ_NOT_AVAILABLE,
        "The quiz is not currently available for attempts.",
        details={"quizId": quiz_id, "reason": reason},
    )


def invalid_configuration(message: str, **details: Any) -> AppError:
    return AppError(422, ErrorCode.INVALID_CONFIGURATION, message, details=details or None)


def configuration_version_unavailable(
    quiz_id: str, configuration_version_id: str | None = None
) -> AppError:
    return AppError(
        409,
        ErrorCode.CONFIGURATION_VERSION_UNAVAILABLE,
        "No usable quiz configuration version is available.",
        details={"quizId": quiz_id, "configurationVersionId": configuration_version_id},
    )


def question_bank_unavailable(
    message: str = "The question bank is currently unavailable.",
) -> AppError:
    return AppError(503, ErrorCode.QUESTION_BANK_UNAVAILABLE, message, retryable=True)


def insufficient_questions(**details: Any) -> AppError:
    return AppError(
        422,
        ErrorCode.INSUFFICIENT_QUESTIONS,
        "The question bank does not contain enough eligible questions "
        "to satisfy the quiz configuration.",
        details=details or None,
    )


def question_unavailable(question_id: str) -> AppError:
    return AppError(
        409,
        ErrorCode.QUESTION_UNAVAILABLE,
        "The question is not available for this attempt.",
        details={"questionId": question_id},
    )


def attempt_not_found(attempt_id: str | None = None) -> AppError:
    return AppError(
        404,
        ErrorCode.ATTEMPT_NOT_FOUND,
        "The attempt does not exist or is not accessible.",
        details=None if attempt_id is None else {"attemptId": attempt_id},
    )


def no_active_attempt(quiz_id: str) -> AppError:
    return AppError(
        404,
        ErrorCode.NO_ACTIVE_ATTEMPT,
        "The learner has no attempt in progress for this quiz.",
        details={"quizId": quiz_id},
    )


def attempt_already_submitted(attempt_id: str, submitted_at: str | None) -> AppError:
    return AppError(
        409,
        ErrorCode.ATTEMPT_ALREADY_SUBMITTED,
        "The attempt has been submitted and is locked.",
        details={"attemptId": attempt_id, "submittedAt": submitted_at},
    )


def attempt_submission_pending(attempt_id: str) -> AppError:
    return AppError(
        409,
        ErrorCode.ATTEMPT_SUBMISSION_PENDING,
        "The attempt is locked while a pending submission is retried.",
        details={"attemptId": attempt_id},
    )


def attempt_expired(attempt_id: str, expires_at: str | None) -> AppError:
    return AppError(
        409,
        ErrorCode.ATTEMPT_EXPIRED,
        "The attempt time limit has elapsed and the attempt has been submitted.",
        details={"attemptId": attempt_id, "expiresAt": expires_at},
    )


def attempt_not_submittable(attempt_id: str, status: str, **details: Any) -> AppError:
    return AppError(
        409,
        ErrorCode.ATTEMPT_NOT_SUBMITTABLE,
        "The attempt cannot be submitted in its current state.",
        details={"attemptId": attempt_id, "status": status, **details},
    )


def invalid_answer(message: str, **details: Any) -> AppError:
    return AppError(422, ErrorCode.INVALID_ANSWER, message, details=details or None)


def answer_revision_conflict(**details: Any) -> AppError:
    return AppError(
        409,
        ErrorCode.ANSWER_REVISION_CONFLICT,
        "The stored answer has changed since the revision supplied by the client.",
        details=details or None,
    )


def question_presentation_violation(message: str, **details: Any) -> AppError:
    return AppError(
        409, ErrorCode.QUESTION_PRESENTATION_VIOLATION, message, details=details or None
    )


def invalid_flag_operation(message: str, **details: Any) -> AppError:
    return AppError(409, ErrorCode.INVALID_FLAG_OPERATION, message, details=details or None)


def duplicate_submission(attempt_id: str, **details: Any) -> AppError:
    return AppError(
        409,
        ErrorCode.DUPLICATE_SUBMISSION,
        "A submission already exists for this attempt.",
        details={"attemptId": attempt_id, **details},
    )


def idempotency_key_reused(idempotency_key: str) -> AppError:
    return AppError(
        409,
        ErrorCode.IDEMPOTENCY_KEY_REUSED,
        "The idempotency key has already been used with a different request payload.",
        details={"idempotencyKey": idempotency_key},
    )


def submission_not_confirmed() -> AppError:
    return AppError(
        400,
        ErrorCode.SUBMISSION_NOT_CONFIRMED,
        'Submission requires explicit confirmation. Send "confirmed": true to submit the attempt.',
    )


def submission_failed(message: str, *, retryable: bool = True, **details: Any) -> AppError:
    return AppError(
        502,
        ErrorCode.SUBMISSION_FAILED,
        message,
        details=details or None,
        retryable=retryable,
    )


def no_pending_submission(attempt_id: str) -> AppError:
    return AppError(
        409,
        ErrorCode.NO_PENDING_SUBMISSION,
        "There is no pending submission to retry for this attempt.",
        details={"attemptId": attempt_id},
    )


def database_error() -> AppError:
    return AppError(
        500,
        ErrorCode.DATABASE_ERROR,
        "A database error prevented the operation from completing.",
        retryable=True,
    )


def internal_error() -> AppError:
    return AppError(500, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred.")
