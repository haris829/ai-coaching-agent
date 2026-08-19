"""UC-04's error codes and failure factories. Same shape as UC-03's: the exception *class* is the
application's one :class:`app.core.errors.AppError`, subclassed only to keep a positional
``(status, code, message)`` signature so the factories read naturally. There is one exception
hierarchy, one handler and one envelope for the whole API, and an architecture test enforces
that."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.errors import AppError as SharedAppError


class ErrorCode(StrEnum):
    """Stable, machine-readable codes UC-04 returns."""

    # ---- the attempt boundary --------------------------------------------
    ATTEMPT_NOT_FOUND = "ATTEMPT_NOT_FOUND"
    ATTEMPT_NOT_SUBMITTED = "ATTEMPT_NOT_SUBMITTED"

    # ---- results ---------------------------------------------------------
    RESULT_NOT_FOUND = "RESULT_NOT_FOUND"
    #: A confirmed score is immutable: re-scoring it is refused, not silently ignored.
    RESULT_ALREADY_SCORED = "RESULT_ALREADY_SCORED"
    SCORING_FAILED = "SCORING_FAILED"

    # ---- infrastructure --------------------------------------------------
    UNAUTHENTICATED = "UNAUTHENTICATED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(SharedAppError):
    """An expected UC-04 failure, safe to render to the client verbatim."""

    def __init__(
        self,
        status: int,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            message,
            status_code=status,
            code=code,
            # Structured context about the failure, not field-level problems, so it belongs in the
            # envelope's `context` slot.
            context=details or None,
            retryable=retryable,
        )

    @property
    def status(self) -> int:
        return self.status_code


def attempt_not_found(attempt_id: str) -> AppError:
    return AppError(
        404,
        ErrorCode.ATTEMPT_NOT_FOUND,
        "The attempt was not found, or it does not belong to this learner.",
        details={"attemptId": attempt_id},
    )


def attempt_not_submitted(attempt_id: str, status: str) -> AppError:
    return AppError(
        409,
        ErrorCode.ATTEMPT_NOT_SUBMITTED,
        "An attempt can only be scored once it has been submitted.",
        details={"attemptId": attempt_id, "attemptStatus": status},
    )


def result_not_found(attempt_id: str) -> AppError:
    return AppError(
        404,
        ErrorCode.RESULT_NOT_FOUND,
        "No result has been recorded for this attempt yet.",
        details={"attemptId": attempt_id},
    )


def result_already_scored(attempt_id: str, **details: Any) -> AppError:
    return AppError(
        409,
        ErrorCode.RESULT_ALREADY_SCORED,
        "This attempt has already been scored, and a confirmed score cannot be changed.",
        details={"attemptId": attempt_id, **details},
    )


def scoring_failed(message: str, *, retryable: bool = True, **details: Any) -> AppError:
    return AppError(
        502, ErrorCode.SCORING_FAILED, message, details=details or None, retryable=retryable
    )


def unauthenticated(message: str = "A learner identity is required.") -> AppError:
    return AppError(401, ErrorCode.UNAUTHENTICATED, message)


def persistence_failed(operation: str) -> AppError:
    return AppError(
        503,
        ErrorCode.PERSISTENCE_FAILED,
        "The result could not be saved because of a temporary problem. Nothing was saved -- "
        "please retry.",
        details={"operation": operation},
        retryable=True,
    )


def internal_error() -> AppError:
    return AppError(500, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred.")
