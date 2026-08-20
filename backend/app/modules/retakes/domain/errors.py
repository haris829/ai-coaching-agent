"""UC-08's own failure taxonomy.

Every failure that is part of normal operation is one of these, so the API layer can render it
directly and a caller can branch on a stable ``code``. Anything else becomes a generic
``INTERNAL_ERROR`` in ``app.core.exception_handlers`` — driver messages, SQL and tracebacks never
reach a client.

Codes overlap deliberately with UC-03's where they mean the same thing (``MAX_ATTEMPTS_REACHED``,
``INSUFFICIENT_QUESTIONS``, ``QUESTION_BANK_UNAVAILABLE``), so a client that already handles a
UC-03 refusal handles the UC-08 one without new branches.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NamedProviderUnavailableError,
    NotFoundError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Identity and lookup
# ---------------------------------------------------------------------------


class LearnerNotFoundError(NotFoundError):
    def __init__(self, learner_id: str) -> None:
        super().__init__("Learner", learner_id, code="LEARNER_NOT_FOUND")


class CourseNotFoundError(NotFoundError):
    def __init__(self, course_id: str) -> None:
        super().__init__("Course", course_id, code="COURSE_NOT_FOUND")


class QuizNotFoundError(NotFoundError):
    def __init__(self, quiz_id: str) -> None:
        super().__init__("Quiz", quiz_id, code="QUIZ_NOT_FOUND")


class AttemptNotFoundError(NotFoundError):
    def __init__(self, attempt_id: str) -> None:
        super().__init__("Attempt", attempt_id, code="ATTEMPT_NOT_FOUND")


class RetakeNotFoundError(NotFoundError):
    def __init__(self, retake_id: str) -> None:
        super().__init__("Retake", retake_id, code="RETAKE_NOT_FOUND")


class GrantNotFoundError(NotFoundError):
    def __init__(self, grant_id: str) -> None:
        super().__init__("Additional attempt grant", grant_id, code="GRANT_NOT_FOUND")


class AttemptOwnershipError(ForbiddenError):
    """The attempt exists but belongs to another learner.

    Refused before any retake logic runs, so a guessed attempt id can never become a retake on
    someone else's history.
    """

    code = "ATTEMPT_NOT_OWNED"

    def __init__(self, attempt_id: str, learner_id: str) -> None:
        super().__init__(
            "This attempt does not belong to the requesting learner.",
            context={"attempt_id": attempt_id, "learner_id": learner_id},
        )


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


class NoAttemptsRemainingError(ConflictError):
    """The allowance is spent. The authoritative refusal §13 requires.

    Carries the guidance text so the user-facing layer can render the administrator-contact
    message without composing its own.
    """

    def __init__(
        self,
        *,
        maximum_attempts: int | None,
        attempts_used: int,
        granted_attempts: int,
        guidance: str,
    ) -> None:
        super().__init__(
            "No attempts remain for this quiz.",
            code="MAX_ATTEMPTS_REACHED",
            context={
                "maximum_attempts": maximum_attempts,
                "granted_attempts": granted_attempts,
                "attempts_used": attempts_used,
                "available_attempts": 0,
                "guidance": guidance,
            },
        )


class PreviousAttemptNotRetakeableError(ConflictError):
    """The attempt named as the one being retaken is not in a state that permits it."""

    def __init__(self, attempt_id: str, status: str) -> None:
        super().__init__(
            "The previous attempt is still in progress, so it cannot be retaken yet.",
            code="PREVIOUS_ATTEMPT_NOT_ELIGIBLE",
            context={"attempt_id": attempt_id, "status": status},
        )


class NoCompletedAttemptError(ConflictError):
    """A retake needs something to retake.

    A learner with no submitted attempt starts their first attempt through UC-03; UC-08 is not a
    second way to begin a quiz.
    """

    def __init__(self, learner_id: str, quiz_id: str) -> None:
        super().__init__(
            "The learner has no completed attempt at this quiz to retake.",
            code="NO_COMPLETED_ATTEMPT",
            context={"learner_id": learner_id, "quiz_id": quiz_id},
        )


class PreviousAttemptQuizMismatchError(ConflictError):
    """The attempt named is real, and the learner's, but belongs to a different quiz.

    Refused rather than quietly retaking whatever quiz the attempt happened to be at: a retake
    request that names two different quizzes is a client defect, and guessing which one was meant
    would create an attempt nobody asked for.
    """

    def __init__(self, attempt_id: str, attempt_quiz_id: str, requested_quiz_id: str) -> None:
        super().__init__(
            "The attempt belongs to a different quiz than the one being retaken.",
            code="ATTEMPT_QUIZ_MISMATCH",
            context={
                "attempt_id": attempt_id,
                "attempt_quiz_id": attempt_quiz_id,
                "requested_quiz_id": requested_quiz_id,
            },
        )


class PreviousAttemptSupersededError(ConflictError):
    """A later attempt exists, so the one named is no longer the attempt to retake.

    Only the learner's most recent submitted attempt can be retaken. Allowing an older one would
    mean numbering the new attempt from stale history and excluding the wrong questions — and
    would give a client a second route to an attempt it had already been refused.
    """

    def __init__(self, attempt_id: str, latest_attempt_id: str) -> None:
        super().__init__(
            "A more recent attempt exists; only the latest completed attempt can be retaken.",
            code="PREVIOUS_ATTEMPT_SUPERSEDED",
            context={"attempt_id": attempt_id, "latest_attempt_id": latest_attempt_id},
        )


class AttemptInProgressError(ConflictError):
    """An attempt is still open. UC-03 permits one open attempt per learner per quiz."""

    def __init__(self, attempt_id: str) -> None:
        super().__init__(
            "An attempt at this quiz is already in progress. "
            "Submit it before starting a retake.",
            code="ATTEMPT_IN_PROGRESS",
            context={"open_attempt_id": attempt_id},
        )


class QuizNotAvailableError(ConflictError):
    def __init__(self, quiz_id: str, reason: str | None) -> None:
        super().__init__(
            "The quiz is not currently available for attempts.",
            code="QUIZ_NOT_AVAILABLE",
            context={"quiz_id": quiz_id, "reason": reason},
        )


# ---------------------------------------------------------------------------
# Configuration and question bank
# ---------------------------------------------------------------------------


class ConfigurationUnavailableError(ConflictError):
    """No usable UC-01 configuration version could be resolved for the retake."""

    def __init__(self, quiz_id: str, configuration_version_id: str | None = None) -> None:
        super().__init__(
            "No usable quiz configuration version is available for a retake.",
            code="CONFIGURATION_VERSION_UNAVAILABLE",
            context={"quiz_id": quiz_id, "configuration_version_id": configuration_version_id},
            retryable=True,
        )


class InvalidConfigurationError(ValidationError):
    """The resolved configuration version cannot produce a deliverable retake."""

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message, code="INVALID_CONFIGURATION", context=context or None)


class AttemptDeliveryUnavailableError(NamedProviderUnavailableError):
    """UC-03 could not be read.

    Added at integration, when UC-03 stopped being a fake and became a module that can genuinely
    be unreachable. Kept distinct from :class:`AttemptCreationFailedError`: this one means the
    attempt module did not answer, which is retryable and implies nothing was written; that one
    means it answered and refused, which needs the learner told why.
    """

    code = "ATTEMPT_DELIVERY_UNAVAILABLE"
    provider = "uc03"
    default_message = "Attempt delivery is currently unavailable."


class QuestionBankUnavailableError(NamedProviderUnavailableError):
    code = "QUESTION_BANK_UNAVAILABLE"
    provider = "uc02"
    default_message = "The question bank is currently unavailable."


class InsufficientQuestionsError(ValidationError):
    """Not enough *eligible* questions exist at all — a different thing from not enough
    *unused* ones, which is handled by falling back rather than failing."""

    def __init__(self, **context: Any) -> None:
        super().__init__(
            "The question bank does not contain enough eligible questions to deliver a retake.",
            code="INSUFFICIENT_QUESTIONS",
            context=context or None,
        )


# ---------------------------------------------------------------------------
# Retake creation
# ---------------------------------------------------------------------------


class RetakeInProgressError(ConflictError):
    """A retake for this same previous attempt is already being created.

    The honest answer to a concurrent duplicate: nothing is broken, and the caller should read the
    winner rather than create a second attempt.
    """

    def __init__(self, retake_id: str, previous_attempt_id: str) -> None:
        super().__init__(
            "A retake for this attempt is already being created.",
            code="RETAKE_IN_PROGRESS",
            context={"retake_id": retake_id, "previous_attempt_id": previous_attempt_id},
            retryable=True,
        )


class AttemptSlotTakenError(ConflictError):
    """Another request reserved this attempt number first.

    Raised by the repository when the ``(learner, quiz, attempt_number)`` uniqueness constraint is
    violated. This is the constraint that makes the allowance hold under concurrency.
    """

    def __init__(self, learner_id: str, quiz_id: str, attempt_number: int) -> None:
        super().__init__(
            "Another request has already reserved this attempt.",
            code="ATTEMPT_SLOT_TAKEN",
            context={
                "learner_id": learner_id,
                "quiz_id": quiz_id,
                "attempt_number": attempt_number,
            },
            retryable=True,
        )


class DuplicateRetakeRequestError(ConflictError):
    """The idempotency key already exists. Callers read the winner instead of overwriting."""

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            "A retake request with this idempotency key already exists.",
            code="DUPLICATE_RETAKE_REQUEST",
            context={"idempotency_key": idempotency_key},
        )


class RetakeRequestNotFoundError(NotFoundError):
    def __init__(self, retake_id: str) -> None:
        super().__init__(
            "Retake request",
            retake_id,
            code="RETAKE_REQUEST_NOT_FOUND",
            context={"retake_id": retake_id},
        )


class RetakeRequestNotReopenableError(ConflictError):
    """Only a FAILED reservation may be reopened. A COMPLETED one is history."""

    def __init__(self, retake_id: str, status: str) -> None:
        super().__init__(
            "Only a failed retake request can be retried.",
            code="RETAKE_REQUEST_NOT_REOPENABLE",
            context={"retake_id": retake_id, "status": status},
        )


class AttemptCreationFailedError(ConflictError):
    """UC-03 could not create the attempt.

    The reservation is released before this is raised, so a retry is safe and the learner has not
    silently lost an attempt.
    """

    status_code = 502
    code = "ATTEMPT_CREATION_FAILED"

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(
            message,
            code="ATTEMPT_CREATION_FAILED",
            context=context or None,
            retryable=True,
        )
        self.status_code = 502


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------


class DuplicateGrantError(ConflictError):
    """The grant idempotency key already exists.

    Not an error condition to be avoided: the grant service catches it and returns the stored
    grant, which is how a retried grant does not grant twice.
    """

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            "A grant with this idempotency key already exists.",
            code="DUPLICATE_GRANT",
            context={"idempotency_key": idempotency_key},
        )


class InvalidGrantSizeError(BadRequestError):
    """A grant must add at least one attempt, and not more than the configured ceiling.

    Zero and negative values are refused rather than clamped: a grant that silently added nothing
    would be reported as a success and an administrator would believe a learner had an attempt they
    do not have (§12).
    """

    code = "INVALID_GRANT_SIZE"

    def __init__(self, requested: object, maximum: int) -> None:
        super().__init__(
            f"The number of additional attempts must be between 1 and {maximum}."
        )
        self.code = "INVALID_GRANT_SIZE"
        self.context = {"requested": requested, "maximum": maximum}


class GrantIdempotencyKeyReusedError(ConflictError):
    """The same key was sent with a different grant.

    Returning the stored grant would be worse than refusing: the administrator would be told their
    grant succeeded while a different one is in force. The same distinction UC-03 draws for
    submission keys.
    """

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            "This idempotency key has already been used for a different grant.",
            code="GRANT_IDEMPOTENCY_KEY_REUSED",
            context={"idempotency_key": idempotency_key},
        )


class GrantAlreadyRevokedError(ConflictError):
    def __init__(self, grant_id: str) -> None:
        super().__init__(
            "This grant has already been revoked.",
            code="GRANT_ALREADY_REVOKED",
            context={"grant_id": grant_id},
        )


class GrantConsumedError(ConflictError):
    """The granted attempts have already been used, so the grant cannot be withdrawn.

    Revoking a spent grant would retroactively push the learner's used count above their
    allowance, which is a state no other rule in the system can produce.
    """

    def __init__(self, grant_id: str, attempts_used: int, entitlement: int) -> None:
        super().__init__(
            "The granted attempts have already been used and cannot be withdrawn.",
            code="GRANT_ALREADY_CONSUMED",
            context={
                "grant_id": grant_id,
                "attempts_used": attempts_used,
                "entitlement_without_grant": entitlement,
            },
        )
